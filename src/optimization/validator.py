"""Independent plan validator for operational and physical constraint verification."""

from __future__ import annotations

from typing import Any, Optional
import pandas as pd

from src.common.config import Config
from src.common.schemas import OptimizationInput, Plan, Violation


def validate_plan(plan: Plan, opt_input: OptimizationInput, config: Config) -> list[Violation]:
    """Independently verifies all physical, operational, and business constraints of a plan.
    
    Returns a list of Violation instances. An empty list means 100% clean plan.
    """
    violations: list[Violation] = []
    
    n_slots = opt_input.n_slots
    slot_hours = opt_input.slot_hours
    turnaround = config.trips.turnaround_slots
    
    vehicles_df = opt_input.vehicles.set_index("vehicle_id")
    trips_df = opt_input.trips.set_index("trip_id")
    elig_df = opt_input.eligibility
    prices_df = opt_input.prices.set_index("slot")
    chargers_df = opt_input.chargers

    # Charger efficiency map
    eff_map = {}
    for _, ch in chargers_df.iterrows():
        eff_map[ch["type"]] = float(ch["efficiency"])

    # 1. Check Trip Assignments & Double Booking
    served_assignments = plan.assignments[plan.assignments["served"] == True]
    veh_trips: dict[str, list[str]] = {}
    for _, ass in served_assignments.iterrows():
        t_id = ass["trip_id"]
        v_id = ass["vehicle_id"]
        if pd.isna(v_id) or not v_id:
            continue
        veh_trips.setdefault(v_id, []).append(t_id)

        # Check eligibility
        m = elig_df[(elig_df["vehicle_id"] == v_id) & (elig_df["trip_id"] == t_id)]
        if m.empty or not bool(m.iloc[0]["eligible"]):
            violations.append(Violation(
                type="ineligible_assignment",
                vehicle_id=v_id,
                trip_id=t_id,
                message=f"Vehicle {v_id} assigned to ineligible trip {t_id}."
            ))

    # Check time overlaps per vehicle
    for v_id, t_ids in veh_trips.items():
        intervals = []
        for t_id in t_ids:
            if t_id in trips_df.index:
                t_row = trips_df.loc[t_id]
                intervals.append((int(t_row["departure_slot"]), int(t_row["return_slot"]) + turnaround, t_id))
        
        intervals.sort(key=lambda x: x[0])
        for i in range(len(intervals) - 1):
            if intervals[i][1] > intervals[i+1][0]:
                violations.append(Violation(
                    type="double_booking",
                    vehicle_id=v_id,
                    trip_id=intervals[i+1][2],
                    slot=intervals[i+1][0],
                    message=f"Vehicle {v_id} double booked between trips {intervals[i][2]} and {intervals[i+1][2]} (including turnaround)."
                ))

    # 2. Check Charging schedule vs Trips and Availability
    charging_df = plan.charging[plan.charging["power_kw_grid"] > 1e-4]
    
    # Active trip slot mask per vehicle
    active_on_trip: dict[tuple[str, int], str] = {}
    for _, ass in served_assignments.iterrows():
        t_id = ass["trip_id"]
        v_id = ass["vehicle_id"]
        if not v_id or pd.isna(v_id) or t_id not in trips_df.index:
            continue
        t_row = trips_df.loc[t_id]
        for s in range(int(t_row["departure_slot"]), int(t_row["return_slot"])):
            active_on_trip[(v_id, s)] = t_id

    for _, chg in charging_df.iterrows():
        v_id = chg["vehicle_id"]
        s = int(chg["slot"])
        p_kw = float(chg["power_kw_grid"])
        c_type = chg["charger_type"]

        # Charging while on trip
        if (v_id, s) in active_on_trip:
            violations.append(Violation(
                type="charge_while_away",
                vehicle_id=v_id,
                trip_id=active_on_trip[(v_id, s)],
                slot=s,
                amount=p_kw,
                message=f"Vehicle {v_id} charging at slot {s} ({p_kw:.1f} kW) while on trip {active_on_trip[(v_id, s)]}."
            ))

        # Charging before available_from_slot
        if v_id in vehicles_df.index:
            avail_slot = int(vehicles_df.loc[v_id]["available_from_slot"])
            if s < avail_slot:
                violations.append(Violation(
                    type="charge_before_available",
                    vehicle_id=v_id,
                    slot=s,
                    amount=p_kw,
                    message=f"Vehicle {v_id} charging at slot {s} before availability at slot {avail_slot}."
                ))

            # DC Compatibility check
            if c_type == "DC":
                max_dc = float(vehicles_df.loc[v_id]["max_dc_kw"])
                if max_dc <= 0:
                    violations.append(Violation(
                        type="incompatible_charger",
                        vehicle_id=v_id,
                        slot=s,
                        message=f"DC-incompatible vehicle {v_id} charging on DC charger."
                    ))

    # 3. Charger Count Limits per type per slot
    charger_cap_map = {}
    for _, cap in opt_input.charger_capacity.iterrows():
        charger_cap_map[(int(cap["slot"]), cap["type"])] = int(cap["available_count"])

    for (s, c_type), group in charging_df.groupby(["slot", "charger_type"]):
        n_charging = len(group["vehicle_id"].unique())
        avail = charger_cap_map.get((int(s), c_type), 0)
        if n_charging > avail:
            violations.append(Violation(
                type="charger_overcapacity",
                slot=int(s),
                amount=float(n_charging - avail),
                message=f"Slot {s}: {n_charging} vehicles charging on {c_type}, exceeding available capacity ({avail})."
            ))

    # 4. Site Power Limit
    site_cap_map = dict(zip(opt_input.site_capacity["slot"], opt_input.site_capacity["site_limit_kw"]))
    for s, group in charging_df.groupby("slot"):
        site_kw = group["power_kw_grid"].sum()
        limit_kw = site_cap_map.get(int(s), config.site.limit_kw)
        if site_kw > limit_kw + 1e-2:
            violations.append(Violation(
                type="site_limit_exceeded",
                slot=int(s),
                amount=float(site_kw - limit_kw),
                message=f"Slot {s}: Site load {site_kw:.1f} kW exceeds limit {limit_kw:.1f} kW."
            ))

    # 5. Independent SOC Simulation & Departure Energy Verification
    # Recompute SOC slot-by-slot from initial energy
    for v_id, v_row in vehicles_df.iterrows():
        e_init = float(v_row["e_init"])
        e_min = float(v_row["e_min"])
        e_max = float(v_row["e_max"])
        
        v_charges = charging_df[charging_df["vehicle_id"] == v_id].set_index("slot")
        v_trips_served = [t for t in veh_trips.get(v_id, []) if t in trips_df.index]

        # Map trip return energy deductions and departure requirements
        trip_dep_req: dict[int, tuple[str, float]] = {}
        trip_ret_deduct: dict[int, float] = {}

        for t_id in v_trips_served:
            t_row = trips_df.loc[t_id]
            dep_s = int(t_row["departure_slot"])
            ret_s = int(t_row["return_slot"])
            
            # lookup required energy from eligibility table
            m = elig_df[(elig_df["vehicle_id"] == v_id) & (elig_df["trip_id"] == t_id)]
            if not m.empty:
                req_dep = float(m.iloc[0]["required_departure_kwh"])
                e_trip = float(m.iloc[0]["energy_kwh"])
            else:
                req_dep = float(t_row.get("required_energy_kwh", 0)) + e_min
                e_trip = float(t_row.get("required_energy_kwh", 0))

            trip_dep_req[dep_s] = (t_id, req_dep)
            trip_ret_deduct[ret_s - 1] = trip_ret_deduct.get(ret_s - 1, 0.0) + e_trip

        cur_e = e_init
        for s in range(n_slots):
            # Check departure energy requirement
            if s in trip_dep_req:
                t_id, req_dep = trip_dep_req[s]
                if cur_e < req_dep - 1e-2:
                    violations.append(Violation(
                        type="insufficient_departure_soc",
                        vehicle_id=v_id,
                        trip_id=t_id,
                        slot=s,
                        amount=float(req_dep - cur_e),
                        message=f"Vehicle {v_id} at slot {s} has {cur_e:.2f} kWh, less than required departure energy {req_dep:.2f} kWh for trip {t_id}."
                    ))

            # Charge energy added
            if s in v_charges.index:
                row_c = v_charges.loc[s]
                if isinstance(row_c, pd.DataFrame):
                    row_c = row_c.iloc[0]
                p_grid = float(row_c["power_kw_grid"])
                c_type = str(row_c["charger_type"])
                eta = eff_map.get(c_type, 0.92)
                e_added = p_grid * slot_hours * eta
            else:
                e_added = 0.0

            # Energy consumed at trip end
            e_consumed = trip_ret_deduct.get(s, 0.0)

            cur_e = cur_e + e_added - e_consumed

            # Check SOC bounds
            if cur_e > e_max + 1e-2:
                violations.append(Violation(
                    type="soc_ceiling_exceeded",
                    vehicle_id=v_id,
                    slot=s,
                    amount=float(cur_e - e_max),
                    message=f"Vehicle {v_id} energy {cur_e:.2f} kWh exceeds ceiling {e_max:.2f} kWh at slot {s}."
                ))
            if cur_e < e_min - 1e-2:
                violations.append(Violation(
                    type="soc_below_reserve",
                    vehicle_id=v_id,
                    slot=s,
                    amount=float(e_min - cur_e),
                    message=f"Vehicle {v_id} energy {cur_e:.2f} kWh is below reserve {e_min:.2f} kWh at slot {s}."
                ))

    return violations
