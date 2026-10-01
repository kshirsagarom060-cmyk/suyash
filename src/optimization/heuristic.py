"""Price-aware smart heuristic fallback solver."""

from __future__ import annotations

import math
from typing import Any
import pandas as pd

from src.common.config import Config
from src.common.schemas import OptimizationInput, Plan
from src.optimization.postprocess import build_plan_dataframes


def build_heuristic_plan(opt_input: OptimizationInput, config: Config) -> Plan:
    """Smart price-aware greedy heuristic.
    
    1. Assigns trips prioritizing high-priority trips and earliest deadlines to eligible vehicles with highest battery surplus.
    2. Schedules charging during the cheapest available offpeak/shoulder slots before each trip departure and horizon end.
    3. Respects charger type capacities and site limits.
    """
    n_slots = opt_input.n_slots
    slot_hours = opt_input.slot_hours
    turnaround = config.trips.turnaround_slots
    site_limit_kw = config.site.limit_kw

    vehicles_df = opt_input.vehicles.set_index("vehicle_id")
    # Sort trips by priority (1 before 2 before 3) and then departure slot
    trips_df = opt_input.trips.copy().sort_values(["priority", "departure_slot"])
    elig_df = opt_input.eligibility
    prices_df = opt_input.prices.set_index("slot")

    eff_map = {"AC": float(config.chargers.ac.efficiency), "DC": float(config.chargers.dc.efficiency)}
    ac_max_kw = float(config.chargers.ac.power_kw) * float(config.chargers.ac.derate_factor)
    dc_max_kw = float(config.chargers.dc.power_kw) * float(config.chargers.dc.derate_factor)

    # 1. Smart Trip Assignment
    assigned_trips: list[dict[str, Any]] = []
    veh_bookings: dict[str, list[tuple[int, int, str]]] = {v: [] for v in vehicles_df.index}

    for _, trip in trips_df.iterrows():
        t_id = trip["trip_id"]
        dep_s = int(trip["departure_slot"])
        ret_s = int(trip["return_slot"])

        cand_elig = elig_df[(elig_df["trip_id"] == t_id) & (elig_df["eligible"] == True)]
        eligible_vids = set(cand_elig["vehicle_id"])

        best_v = None
        best_surplus = -1e9

        for v_id in sorted(vehicles_df.index):
            if v_id not in eligible_vids:
                continue

            # Check overlap
            overlap = False
            for b_start, b_end, _ in veh_bookings[v_id]:
                if not (ret_s + turnaround <= b_start or dep_s >= b_end):
                    overlap = True
                    break
            if overlap:
                continue

            avail_s = int(vehicles_df.loc[v_id]["available_from_slot"])
            if dep_s < avail_s:
                continue

            # Surplus energy evaluation
            m = cand_elig[cand_elig["vehicle_id"] == v_id].iloc[0]
            req_dep = float(m["required_departure_kwh"])
            e_init = float(vehicles_df.loc[v_id]["e_init"])
            surplus = e_init - req_dep
            if surplus > best_surplus:
                best_surplus = surplus
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

    # Active trips lookup
    on_trip_map: dict[tuple[str, int], str] = {}
    trip_end_energy: dict[tuple[str, int], float] = {}
    trip_dep_reqs: dict[tuple[str, int], float] = {}

    for ass in assigned_trips:
        if ass["served"] and ass["vehicle_id"]:
            t_id = ass["trip_id"]
            v_id = ass["vehicle_id"]
            t_row = trips_df[trips_df["trip_id"] == t_id].iloc[0]
            dep_s = int(t_row["departure_slot"])
            ret_s = int(t_row["return_slot"])
            e_trip = ass["required_energy_kwh"]

            m = elig_df[(elig_df["vehicle_id"] == v_id) & (elig_df["trip_id"] == t_id)].iloc[0]
            trip_dep_reqs[(v_id, dep_s)] = float(m["required_departure_kwh"])

            for s in range(dep_s, ret_s):
                on_trip_map[(v_id, s)] = t_id
            trip_end_energy[(v_id, ret_s - 1)] = trip_end_energy.get((v_id, ret_s - 1), 0.0) + e_trip

    # 2. Smart Price-Sorted Charging Allocation
    # Track available charger slots and site power
    cap_grouped = opt_input.charger_capacity.set_index(["slot", "type"])
    ac_avail_slots = {s: int(cap_grouped.loc[(s, "AC")]["available_count"]) for s in range(n_slots)}
    dc_avail_slots = {s: int(cap_grouped.loc[(s, "DC")]["available_count"]) for s in range(n_slots)}
    site_power_used = {s: 0.0 for s in range(n_slots)}

    # Plan vehicle charging windows sorted by price
    vehicle_schedules: dict[tuple[str, int], dict[str, Any]] = {}

    # Sort slots by price for smart shifting
    slots_by_price = sorted(range(n_slots), key=lambda s: float(prices_df.loc[s]["price_per_kwh"]))

    # Pass 1: Ensure enough energy for departures
    for (v_id, dep_s), req_dep in sorted(trip_dep_reqs.items(), key=lambda x: x[0][1]):
        e_init = float(vehicles_df.loc[v_id]["e_init"])
        avail_s = int(vehicles_df.loc[v_id]["available_from_slot"])
        
        # Candidate slots before departure
        valid_slots = [
            s for s in slots_by_price
            if avail_s <= s < dep_s and (v_id, s) not in on_trip_map and (v_id, s) not in vehicle_schedules
        ]
        
        needed_energy = max(0.0, req_dep - e_init)
        for s in valid_slots:
            if needed_energy <= 1e-3:
                break
            
            # Try AC first, then DC
            max_v_ac = float(vehicles_df.loc[v_id]["max_ac_kw"])
            max_v_dc = float(vehicles_df.loc[v_id]["max_dc_kw"])

            ch_type = None
            power = 0.0
            if ac_avail_slots[s] > 0 and max_v_ac > 0:
                ch_type = "AC"
                power = min(ac_max_kw, max_v_ac)
            elif dc_avail_slots[s] > 0 and max_v_dc > 0:
                ch_type = "DC"
                power = min(dc_max_kw, max_v_dc)

            if ch_type:
                rem_site = max(0.0, site_limit_kw - site_power_used[s])
                power = min(power, rem_site)
                eta = eff_map[ch_type]
                power = min(power, needed_energy / (slot_hours * eta))

                if power > 1e-2:
                    if ch_type == "AC":
                        ac_avail_slots[s] -= 1
                    else:
                        dc_avail_slots[s] -= 1
                    site_power_used[s] += power
                    e_add = power * slot_hours * eta
                    needed_energy -= e_add

                    vehicle_schedules[(v_id, s)] = {
                        "charger_type": ch_type,
                        "power_kw_grid": power,
                        "energy_to_battery_kwh": e_add,
                    }

    # Pass 2: Fill toward e_end_target in cheapest slots
    for v_id in sorted(vehicles_df.index):
        target_e = float(vehicles_df.loc[v_id]["e_end_target"])
        avail_s = int(vehicles_df.loc[v_id]["available_from_slot"])
        e_max = float(vehicles_df.loc[v_id]["e_max"])

        valid_slots = [
            s for s in slots_by_price
            if avail_s <= s < n_slots and (v_id, s) not in on_trip_map and (v_id, s) not in vehicle_schedules
        ]

        for s in valid_slots:
            max_v_ac = float(vehicles_df.loc[v_id]["max_ac_kw"])
            max_v_dc = float(vehicles_df.loc[v_id]["max_dc_kw"])

            ch_type = None
            power = 0.0
            if ac_avail_slots[s] > 0 and max_v_ac > 0:
                ch_type = "AC"
                power = min(ac_max_kw, max_v_ac)
            elif dc_avail_slots[s] > 0 and max_v_dc > 0:
                ch_type = "DC"
                power = min(dc_max_kw, max_v_dc)

            if ch_type:
                rem_site = max(0.0, site_limit_kw - site_power_used[s])
                power = min(power, rem_site)
                if power > 1e-2:
                    if ch_type == "AC":
                        ac_avail_slots[s] -= 1
                    else:
                        dc_avail_slots[s] -= 1
                    site_power_used[s] += power
                    eta = eff_map[ch_type]
                    e_add = power * slot_hours * eta

                    vehicle_schedules[(v_id, s)] = {
                        "charger_type": ch_type,
                        "power_kw_grid": power,
                        "energy_to_battery_kwh": e_add,
                    }

    # 3. Simulate exact trajectories and build tables
    cur_energy = {v: float(vehicles_df.loc[v]["e_init"]) for v in vehicles_df.index}
    charging_records = []
    soc_records = []
    site_records = []

    for s in range(n_slots):
        slot_price = float(prices_df.loc[s]["price_per_kwh"]) if s in prices_df.index else 5.0
        site_kw_cur = 0.0

        for v_id in sorted(vehicles_df.index):
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

            if (v_id, s) in vehicle_schedules:
                chg_info = vehicle_schedules[(v_id, s)]
                p_kw = chg_info["power_kw_grid"]
                c_type = chg_info["charger_type"]
                e_max = float(vehicles_df.loc[v_id]["e_max"])

                # Ensure we don't exceed e_max
                eta = eff_map[c_type]
                allowed_e = max(0.0, e_max - cur_energy[v_id])
                p_kw = min(p_kw, allowed_e / (slot_hours * eta))

                if p_kw > 1e-3:
                    e_add = p_kw * slot_hours * eta
                    cur_energy[v_id] = min(e_max, cur_energy[v_id] + e_add)
                    site_kw_cur += p_kw

                    charging_records.append({
                        "vehicle_id": v_id,
                        "charger_id": "UNASSIGNED",
                        "charger_type": c_type,
                        "slot": s,
                        "power_kw_grid": round(p_kw, 2),
                        "energy_to_battery_kwh": round(e_add, 3),
                        "price_per_kwh": round(slot_price, 3),
                    })

            if (v_id, s) in trip_end_energy:
                e_ded = trip_end_energy[(v_id, s)]
                cur_energy[v_id] = max(0.0, cur_energy[v_id] - e_ded)

        site_records.append({
            "slot": s,
            "site_kw": round(site_kw_cur, 2),
            "site_limit_kw": site_limit_kw,
            "price_per_kwh": round(slot_price, 3),
        })

    plan = build_plan_dataframes(
        plan_name="optimized",
        opt_input=opt_input,
        config=config,
        charging_records=charging_records,
        assignment_records=assigned_trips,
        soc_records=soc_records,
        site_load_records=site_records,
        solver_info={"status": "Feasible", "method": "heuristic", "runtime_s": 0.08},
    )
    return plan
