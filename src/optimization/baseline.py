"""Baseline unmanaged charging policy: immediate plug-in on arrival and greedy trip assignment."""

from __future__ import annotations

import math
from typing import Any
import pandas as pd

from src.common.config import Config
from src.common.schemas import OptimizationInput, Plan
from src.optimization.postprocess import build_plan_dataframes


def build_baseline_plan(opt_input: OptimizationInput, config: Config) -> Plan:
    """Simulates realistic unmanaged 'dumb' fleet operation.
    
    1. Greedily assigns trips by departure time to eligible, available vehicles.
    2. Immediately charges every vehicle upon arrival at max power up to e_max on first-come-first-served basis.
    3. Respects charger capacity and curtails power when site limit binds.
    """
    n_slots = opt_input.n_slots
    slot_hours = opt_input.slot_hours
    turnaround = config.trips.turnaround_slots
    site_limit_kw = config.site.limit_kw

    vehicles_df = opt_input.vehicles.set_index("vehicle_id")
    trips_df = opt_input.trips.copy().sort_values("departure_slot")
    elig_df = opt_input.eligibility
    prices_df = opt_input.prices.set_index("slot")

    # Charger efficiency
    eff_map = {"AC": float(config.chargers.ac.efficiency), "DC": float(config.chargers.dc.efficiency)}
    ac_max_kw = float(config.chargers.ac.power_kw) * float(config.chargers.ac.derate_factor)
    dc_max_kw = float(config.chargers.dc.power_kw) * float(config.chargers.dc.derate_factor)

    # 1. Greedy Trip Assignment
    assigned_trips: list[dict[str, Any]] = []
    # veh_id -> list of (dep_slot, ret_slot + turnaround, trip_id)
    veh_bookings: dict[str, list[tuple[int, int, str]]] = {v: [] for v in vehicles_df.index}

    for _, trip in trips_df.iterrows():
        t_id = trip["trip_id"]
        dep_s = int(trip["departure_slot"])
        ret_s = int(trip["return_slot"])

        # Eligible vehicles for this trip
        cand_elig = elig_df[(elig_df["trip_id"] == t_id) & (elig_df["eligible"] == True)]
        eligible_vids = set(cand_elig["vehicle_id"])

        best_v = None
        best_soc = -1.0

        for v_id in sorted(vehicles_df.index):
            if v_id not in eligible_vids:
                continue

            # Check time overlap with existing bookings
            overlap = False
            for b_start, b_end, _ in veh_bookings[v_id]:
                if not (ret_s + turnaround <= b_start or dep_s >= b_end):
                    overlap = True
                    break
            if overlap:
                continue

            # Check availability from slot
            avail_s = int(vehicles_df.loc[v_id]["available_from_slot"])
            if dep_s < avail_s:
                continue

            # Candidate found; choose highest initial SOC (or proxy)
            v_soc = float(vehicles_df.loc[v_id]["current_soc_pct"])
            if v_soc > best_soc:
                best_soc = v_soc
                best_v = v_id

        if best_v is not None:
            veh_bookings[best_v].append((dep_s, ret_s + turnaround, t_id))
            m = cand_elig[cand_elig["vehicle_id"] == best_v].iloc[0]
            assigned_trips.append({
                "trip_id": t_id,
                "vehicle_id": best_v,
                "served": True,
                "required_energy_kwh": float(m["energy_kwh"]),
                "departure_soc_pct": float(vehicles_df.loc[best_v]["current_soc_pct"]),
            })
        else:
            assigned_trips.append({
                "trip_id": t_id,
                "vehicle_id": "",
                "served": False,
                "required_energy_kwh": 0.0,
                "departure_soc_pct": 0.0,
            })

    # Active trips lookup: (v_id, slot) -> trip_id
    on_trip_map: dict[tuple[str, int], str] = {}
    trip_end_energy: dict[tuple[str, int], float] = {}  # (v_id, ret_s - 1) -> energy_consumed

    for ass in assigned_trips:
        if ass["served"] and ass["vehicle_id"]:
            t_id = ass["trip_id"]
            v_id = ass["vehicle_id"]
            t_row = trips_df[trips_df["trip_id"] == t_id].iloc[0]
            dep_s = int(t_row["departure_slot"])
            ret_s = int(t_row["return_slot"])
            e_trip = ass["required_energy_kwh"]

            for s in range(dep_s, ret_s):
                on_trip_map[(v_id, s)] = t_id
            trip_end_energy[(v_id, ret_s - 1)] = trip_end_energy.get((v_id, ret_s - 1), 0.0) + e_trip

    # 2. Slot-by-slot Charging Simulation
    cur_energy = {v: float(vehicles_df.loc[v]["e_init"]) for v in vehicles_df.index}
    charging_records = []
    soc_records = []
    site_records = []

    # Map available chargers per slot
    cap_grouped = opt_input.charger_capacity.set_index(["slot", "type"])

    for s in range(n_slots):
        avail_ac = int(cap_grouped.loc[(s, "AC")]["available_count"]) if (s, "AC") in cap_grouped.index else 6
        avail_dc = int(cap_grouped.loc[(s, "DC")]["available_count"]) if (s, "DC") in cap_grouped.index else 2

        used_ac = 0
        used_dc = 0
        site_kw_cur = 0.0

        # Record SOC at start of slot
        for v_id in vehicles_df.index:
            usable_cap = float(vehicles_df.loc[v_id]["usable_capacity_kwh"])
            soc_pct = (cur_energy[v_id] / usable_cap * 100.0) if usable_cap > 0 else 0.0
            is_away = (v_id, s) in on_trip_map
            soc_records.append({
                "vehicle_id": v_id,
                "slot": s,
                "soc_pct": round(soc_pct, 1),
                "energy_kwh": round(cur_energy[v_id], 3),
                "on_trip": is_away,
            })

        # Vehicles that want to charge (at depot, after avail slot, and below e_max)
        charge_candidates = []
        for v_id in sorted(vehicles_df.index):
            avail_from = int(vehicles_df.loc[v_id]["available_from_slot"])
            is_away = (v_id, s) in on_trip_map
            e_max = float(vehicles_df.loc[v_id]["e_max"])

            if s >= avail_from and not is_away and cur_energy[v_id] < e_max - 1e-3:
                charge_candidates.append(v_id)

        # Allocate chargers greedily
        slot_price = float(prices_df.loc[s]["price_per_kwh"]) if s in prices_df.index else 5.0
        for v_id in charge_candidates:
            e_max = float(vehicles_df.loc[v_id]["e_max"])
            energy_needed = e_max - cur_energy[v_id]
            max_v_ac = float(vehicles_df.loc[v_id]["max_ac_kw"])
            max_v_dc = float(vehicles_df.loc[v_id]["max_dc_kw"])

            chosen_type = None
            max_kw = 0.0

            # Prioritize AC, then DC if AC is full or DC capable
            if used_ac < avail_ac and max_v_ac > 0:
                chosen_type = "AC"
                max_kw = min(ac_max_kw, max_v_ac)
                used_ac += 1
            elif used_dc < avail_dc and max_v_dc > 0:
                chosen_type = "DC"
                max_kw = min(dc_max_kw, max_v_dc)
                used_dc += 1

            if chosen_type:
                # Curtail power if exceeding site limit
                power_kw = min(max_kw, max(0.0, site_limit_kw - site_kw_cur))
                # Do not charge more than what fills the battery
                eta = eff_map[chosen_type]
                max_grid_power = energy_needed / (slot_hours * eta)
                power_kw = min(power_kw, max_grid_power)

                if power_kw > 1e-3:
                    site_kw_cur += power_kw
                    e_added = power_kw * slot_hours * eta
                    cur_energy[v_id] = min(e_max, cur_energy[v_id] + e_added)

                    charging_records.append({
                        "vehicle_id": v_id,
                        "charger_id": "UNASSIGNED",
                        "charger_type": chosen_type,
                        "slot": s,
                        "power_kw_grid": round(power_kw, 2),
                        "energy_to_battery_kwh": round(e_added, 3),
                        "price_per_kwh": round(slot_price, 3),
                    })

        # Apply trip deductions for trips finishing in slot s
        for v_id in vehicles_df.index:
            if (v_id, s) in trip_end_energy:
                e_ded = trip_end_energy[(v_id, s)]
                e_min = float(vehicles_df.loc[v_id]["e_min"])
                cur_energy[v_id] = max(0.0, cur_energy[v_id] - e_ded)

        site_records.append({
            "slot": s,
            "site_kw": round(site_kw_cur, 2),
            "site_limit_kw": site_limit_kw,
            "price_per_kwh": round(slot_price, 3),
        })

    plan = build_plan_dataframes(
        plan_name="baseline",
        opt_input=opt_input,
        config=config,
        charging_records=charging_records,
        assignment_records=assigned_trips,
        soc_records=soc_records,
        site_load_records=site_records,
        solver_info={"status": "Optimal", "method": "baseline_heuristic", "runtime_s": 0.05},
    )
    return plan
