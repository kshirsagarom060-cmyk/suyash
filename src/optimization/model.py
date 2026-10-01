"""PuLP Mixed Integer Linear Programming (MILP) optimization model."""

from __future__ import annotations

import sys
import time
from typing import Any, Optional
import pulp
import pandas as pd

from src.common.config import Config
from src.common.schemas import OptimizationInput, Plan
from src.optimization.postprocess import build_plan_dataframes


def build_and_solve_milp(opt_input: OptimizationInput, config: Config) -> tuple[Optional[Plan], dict[str, Any]]:
    """Constructs and solves the joint fleet charging and trip assignment MILP model using PuLP."""
    start_time = time.perf_counter()
    
    n_slots = opt_input.n_slots
    slot_hours = opt_input.slot_hours
    turnaround = config.trips.turnaround_slots
    
    vehicles_df = opt_input.vehicles.set_index("vehicle_id")
    trips_df = opt_input.trips.set_index("trip_id")
    elig_df = opt_input.eligibility
    prices_df = opt_input.prices.set_index("slot")
    chargers_df = opt_input.chargers

    # Efficiencies and capacities
    eff_map = {"AC": float(config.chargers.ac.efficiency), "DC": float(config.chargers.dc.efficiency)}
    ac_max_kw = float(config.chargers.ac.power_kw) * float(config.chargers.ac.derate_factor)
    dc_max_kw = float(config.chargers.dc.power_kw) * float(config.chargers.dc.derate_factor)

    cap_grouped = opt_input.charger_capacity.set_index(["slot", "type"])
    site_cap_map = dict(zip(opt_input.site_capacity["slot"], opt_input.site_capacity["site_limit_kw"]))

    cost_params = opt_input.cost_params
    demand_charge = float(cost_params.get("demand_charge_per_kw", 150.0))
    wear_base = float(cost_params.get("wear_cost_per_kwh_throughput", 0.8))
    wear_high_rate = float(cost_params.get("wear_cost_per_kwh_slot_above_threshold", 0.02))
    penalties = cost_params.get("unserved_trip_penalty", {1: 100000.0, 2: 30000.0, 3: 8000.0})
    end_target_penalty_rate = float(cost_params.get("end_target_shortfall_penalty_per_kwh", 5.0))

    w_unavail = float(config.weights.unavailability)
    w_wear = float(config.weights.battery_wear)
    w_viol = float(config.weights.violations)

    # Initialize PuLP Problem
    prob = pulp.LpProblem("EV_Fleet_Optimization", pulp.LpMinimize)

    # Sets
    V = list(vehicles_df.index)
    J = list(trips_df.index)
    T = list(range(n_slots))
    G = ["AC", "DC"]

    # Decision variables
    # a[v, j] in {0, 1}
    a = {}
    for _, row in elig_df[elig_df["eligible"] == True].iterrows():
        v_id = row["vehicle_id"]
        t_id = row["trip_id"]
        a[(v_id, t_id)] = pulp.LpVariable(f"a_{v_id}_{t_id}", cat=pulp.LpBinary)

    # u[j] in {0, 1}
    u = {j: pulp.LpVariable(f"u_{j}", cat=pulp.LpBinary) for j in J}

    # y[v, g, t] in {0, 1} and p[v, g, t] >= 0
    y = {}
    p = {}
    P_max = {}

    for v in V:
        max_ac = float(vehicles_df.loc[v]["max_ac_kw"])
        max_dc = float(vehicles_df.loc[v]["max_dc_kw"])
        avail_s = int(vehicles_df.loc[v]["available_from_slot"])

        for t in T:
            # AC
            p_ac = min(ac_max_kw, max_ac) if t >= avail_s else 0.0
            if p_ac > 0:
                y[(v, "AC", t)] = pulp.LpVariable(f"y_{v}_AC_{t}", cat=pulp.LpBinary)
                p[(v, "AC", t)] = pulp.LpVariable(f"p_{v}_AC_{t}", lowBound=0.0, upBound=p_ac)
                P_max[(v, "AC", t)] = p_ac

            # DC
            p_dc = min(dc_max_kw, max_dc) if (t >= avail_s and max_dc > 0) else 0.0
            if p_dc > 0:
                y[(v, "DC", t)] = pulp.LpVariable(f"y_{v}_DC_{t}", cat=pulp.LpBinary)
                p[(v, "DC", t)] = pulp.LpVariable(f"p_{v}_DC_{t}", lowBound=0.0, upBound=p_dc)
                P_max[(v, "DC", t)] = p_dc

    # e[v, t] for t in 0..n_slots
    e = {}
    for v in V:
        e_min = float(vehicles_df.loc[v]["e_min"])
        e_max = float(vehicles_df.loc[v]["e_max"])
        e_init_val = float(vehicles_df.loc[v]["e_init"])
        for t in range(n_slots + 1):
            e[(v, t)] = pulp.LpVariable(f"e_{v}_{t}", lowBound=0.0, upBound=e_max)

    # h[v, t] >= 0
    h = {}
    for v in V:
        for t in T:
            h[(v, t)] = pulp.LpVariable(f"h_{v}_{t}", lowBound=0.0)

    # s_end[v] >= 0
    s_end = {v: pulp.LpVariable(f"s_end_{v}", lowBound=0.0) for v in V}

    # peak >= 0
    peak = pulp.LpVariable("peak_site_kw", lowBound=0.0)

    # CONSTRAINTS

    # 1. Trip coverage: sum_v a[v, j] + u[j] = 1
    for j in J:
        assigned_vars = [a[(v, j)] for v in V if (v, j) in a]
        prob += (pulp.lpSum(assigned_vars) + u[j] == 1, f"trip_coverage_{j}")

    # 2. No double booking: for each v, t: sum_{j active at t} a[v, j] <= 1
    for v in V:
        for t in T:
            active_trips_at_t = []
            for j in J:
                if (v, j) in a:
                    dep = int(trips_df.loc[j]["departure_slot"])
                    ret = int(trips_df.loc[j]["return_slot"]) + turnaround
                    if dep <= t < ret:
                        active_trips_at_t.append(a[(v, j)])
            if active_trips_at_t:
                prob += (pulp.lpSum(active_trips_at_t) <= 1, f"no_double_booking_{v}_{t}")

    # 3. Away means no charging: sum_g y[v, g, t] <= 1 - sum_{j on trip at t} a[v, j]
    for v in V:
        for t in T:
            active_trip_vars = []
            for j in J:
                if (v, j) in a:
                    dep = int(trips_df.loc[j]["departure_slot"])
                    ret = int(trips_df.loc[j]["return_slot"])
                    if dep <= t < ret:
                        active_trip_vars.append(a[(v, j)])

            y_vars = [y[(v, g, t)] for g in G if (v, g, t) in y]
            if y_vars:
                if active_trip_vars:
                    prob += (pulp.lpSum(y_vars) + pulp.lpSum(active_trip_vars) <= 1, f"away_no_charge_{v}_{t}")
                else:
                    prob += (pulp.lpSum(y_vars) <= 1, f"one_conn_{v}_{t}")

    # 4. Charger type capacity: sum_v y[v, g, t] <= N[g, t]
    for g in G:
        for t in T:
            y_type_t = [y[(v, g, t)] for v in V if (v, g, t) in y]
            n_avail = int(cap_grouped.loc[(t, g)]["available_count"]) if (t, g) in cap_grouped.index else 0
            if y_type_t:
                prob += (pulp.lpSum(y_type_t) <= n_avail, f"charger_cap_{g}_{t}")

    # 5. Power bounds: p[v, g, t] <= P_max * y[v, g, t]
    for key, p_var in p.items():
        v, g, t = key
        prob += (p_var <= P_max[key] * y[key], f"power_bound_{v}_{g}_{t}")

    # 6. Site limit & Peak definition
    for t in T:
        p_t = [p[(v, g, t)] for v in V for g in G if (v, g, t) in p]
        limit = site_cap_map.get(t, config.site.limit_kw)
        if p_t:
            prob += (pulp.lpSum(p_t) <= limit, f"site_limit_{t}")
            prob += (peak >= pulp.lpSum(p_t), f"peak_def_{t}")

    # 7. Energy dynamics & initial state
    for v in V:
        e_init_val = float(vehicles_df.loc[v]["e_init"])
        prob += (e[(v, 0)] == e_init_val, f"initial_energy_{v}")

        for t in T:
            charge_in = pulp.lpSum([p[(v, g, t)] * slot_hours * eff_map[g] for g in G if (v, g, t) in p])
            # Trip return deductions
            trip_out = []
            for j in J:
                if (v, j) in a:
                    ret = int(trips_df.loc[j]["return_slot"])
                    if ret - 1 == t:
                        m = elig_df[(elig_df["vehicle_id"] == v) & (elig_df["trip_id"] == j)].iloc[0]
                        e_trip = float(m["energy_kwh"])
                        trip_out.append(a[(v, j)] * e_trip)

            prob += (
                e[(v, t + 1)] == e[(v, t)] + charge_in - pulp.lpSum(trip_out),
                f"energy_dynamic_{v}_{t}"
            )

    # 8. Enough energy to depart: e[v, dep[j]] >= R[v, j] * a[v, j]
    for (v, j), a_var in a.items():
        dep = int(trips_df.loc[j]["departure_slot"])
        m = elig_df[(elig_df["vehicle_id"] == v) & (elig_df["trip_id"] == j)].iloc[0]
        req_dep = float(m["required_departure_kwh"])
        prob += (e[(v, dep)] >= req_dep * a_var, f"departure_energy_{v}_{j}")

    # 9. High-SOC dwell: h[v, t] >= e[v, t] - e_high[v]
    for v in V:
        e_high_val = float(vehicles_df.loc[v].get("e_high", 0.8 * float(vehicles_df.loc[v]["usable_capacity_kwh"])))
        for t in T:
            prob += (h[(v, t)] >= e[(v, t)] - e_high_val, f"high_soc_dwell_{v}_{t}")

    # 10. End of horizon readiness: e[v, n_slots] + s_end[v] >= e_end[v]
    for v in V:
        e_end_val = float(vehicles_df.loc[v]["e_end_target"])
        prob += (e[(v, n_slots)] + s_end[v] >= e_end_val, f"end_readiness_{v}")

    # OBJECTIVE FUNCTION
    # 1. Electricity cost
    elec_cost_terms = []
    for t in T:
        price_t = float(prices_df.loc[t]["price_per_kwh"]) if t in prices_df.index else 5.0
        p_t = [p[(v, g, t)] for v in V for g in G if (v, g, t) in p]
        if p_t:
            elec_cost_terms.append(price_t * slot_hours * pulp.lpSum(p_t))

    # 2. Demand cost
    demand_cost_term = demand_charge * peak

    # 3. Battery wear cost
    wear_terms = []
    for v in V:
        soh = float(vehicles_df.loc[v].get("soh", 1.0))
        v_wear_rate = wear_base * (1.0 / max(0.1, soh))
        for t in T:
            for g in G:
                if (v, g, t) in p:
                    wear_terms.append(v_wear_rate * eff_map[g] * slot_hours * p[(v, g, t)])
            wear_terms.append(wear_high_rate * slot_hours * h[(v, t)])

    # 4. Unserved trip penalties
    unserved_terms = []
    for j in J:
        pri = int(trips_df.loc[j]["priority"])
        pen = float(penalties.get(pri, 30000.0))
        unserved_terms.append(pen * u[j])

    # 5. End shortfall penalty
    shortfall_terms = [end_target_penalty_rate * s_end[v] for v in V]

    # Tiny tie-breaker to prevent erratic session fragmentation
    tie_breaker = [1e-4 * (t + 1) * p[(v, g, t)] for (v, g, t) in p]

    total_obj = (
        pulp.lpSum(elec_cost_terms)
        + demand_cost_term
        + w_wear * pulp.lpSum(wear_terms)
        + w_unavail * pulp.lpSum(unserved_terms)
        + w_viol * pulp.lpSum(shortfall_terms)
        + pulp.lpSum(tie_breaker)
    )
    prob += total_obj

    # Solve with CBC
    solver_name = config.solver.name.lower()
    time_limit = config.solver.time_limit_s
    gap = config.solver.mip_gap
    threads = config.solver.threads
    if sys.platform == "win32":
        threads = None

    cbc_solver = pulp.PULP_CBC_CMD(
        timeLimit=time_limit,
        gapRel=gap,
        threads=threads,
        msg=False,
    )

    solve_status = prob.solve(cbc_solver)
    runtime = time.perf_counter() - start_time
    status_str = pulp.LpStatus[prob.status]

    solver_info = {
        "status": status_str,
        "objective": round(float(pulp.value(prob.objective)) if prob.objective is not None and prob.status == 1 else 0.0, 2),
        "runtime_s": round(runtime, 3),
        "n_variables": len(prob.variables()),
        "n_constraints": len(prob.constraints),
        "method": "milp_pulp_cbc",
    }

    if status_str not in ["Optimal", "Feasible"]:
        return None, solver_info

    # Extract variable values into Plan
    charging_records = []
    for (v, g, t), p_var in p.items():
        p_val = float(p_var.varValue or 0.0)
        if p_val > 1e-3:
            slot_price = float(prices_df.loc[t]["price_per_kwh"]) if t in prices_df.index else 5.0
            e_bat = p_val * slot_hours * eff_map[g]
            charging_records.append({
                "vehicle_id": v,
                "charger_id": "UNASSIGNED",
                "charger_type": g,
                "slot": t,
                "power_kw_grid": round(p_val, 2),
                "energy_to_battery_kwh": round(e_bat, 3),
                "price_per_kwh": round(slot_price, 3),
            })

    assignment_records = []
    for j in J:
        assigned_v = None
        for v in V:
            if (v, j) in a and round(float(a[(v, j)].varValue or 0.0)) == 1:
                assigned_v = v
                break

        if assigned_v is not None:
            m = elig_df[(elig_df["vehicle_id"] == assigned_v) & (elig_df["trip_id"] == j)].iloc[0]
            dep_s = int(trips_df.loc[j]["departure_slot"])
            dep_e = float(e[(assigned_v, dep_s)].varValue or 0.0)
            usable_cap = float(vehicles_df.loc[assigned_v]["usable_capacity_kwh"])
            dep_soc = (dep_e / usable_cap * 100.0) if usable_cap > 0 else 0.0

            assignment_records.append({
                "trip_id": j,
                "vehicle_id": assigned_v,
                "served": True,
                "required_energy_kwh": float(m["energy_kwh"]),
                "departure_soc_pct": round(dep_soc, 1),
            })
        else:
            assignment_records.append({
                "trip_id": j,
                "vehicle_id": "",
                "served": False,
                "required_energy_kwh": 0.0,
                "departure_soc_pct": 0.0,
            })

    # Trip active lookup
    on_trip_active = set()
    for ass in assignment_records:
        if ass["served"] and ass["vehicle_id"]:
            t_id = ass["trip_id"]
            v_id = ass["vehicle_id"]
            dep_s = int(trips_df.loc[t_id]["departure_slot"])
            ret_s = int(trips_df.loc[t_id]["return_slot"])
            for s in range(dep_s, ret_s):
                on_trip_active.add((v_id, s))

    soc_records = []
    for v in V:
        usable_cap = float(vehicles_df.loc[v]["usable_capacity_kwh"])
        for t in T:
            e_val = float(e[(v, t)].varValue or 0.0)
            soc_pct = (e_val / usable_cap * 100.0) if usable_cap > 0 else 0.0
            soc_records.append({
                "vehicle_id": v,
                "slot": t,
                "soc_pct": round(soc_pct, 1),
                "energy_kwh": round(e_val, 3),
                "on_trip": (v, t) in on_trip_active,
            })

    site_records = []
    for t in T:
        p_t_vals = [float(p[(v, g, t)].varValue or 0.0) for v in V for g in G if (v, g, t) in p]
        site_kw = sum(p_t_vals)
        slot_price = float(prices_df.loc[t]["price_per_kwh"]) if t in prices_df.index else 5.0
        site_records.append({
            "slot": t,
            "site_kw": round(site_kw, 2),
            "site_limit_kw": site_cap_map.get(t, config.site.limit_kw),
            "price_per_kwh": round(slot_price, 3),
        })

    plan = build_plan_dataframes(
        plan_name="optimized",
        opt_input=opt_input,
        config=config,
        charging_records=charging_records,
        assignment_records=assignment_records,
        soc_records=soc_records,
        site_load_records=site_records,
        solver_info=solver_info,
    )
    return plan, solver_info
