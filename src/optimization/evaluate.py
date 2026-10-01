"""Single cost evaluation and KPI computation function for all plans."""

from __future__ import annotations

from typing import Any
import pandas as pd

from src.common.config import Config
from src.common.schemas import OptimizationInput, Plan


def evaluate_plan(plan: Plan, opt_input: OptimizationInput, config: Config) -> dict[str, Any]:
    """Computes transparent, standardized cost breakdown and operational KPIs for a plan."""
    slot_hours = opt_input.slot_hours
    prices_df = opt_input.prices.set_index("slot")
    cost_params = opt_input.cost_params
    demand_charge = float(cost_params.get("demand_charge_per_kw", 150.0))
    wear_base = float(cost_params.get("wear_cost_per_kwh_throughput", 0.8))
    wear_high_rate = float(cost_params.get("wear_cost_per_kwh_slot_above_threshold", 0.02))
    penalties = cost_params.get("unserved_trip_penalty", {1: 100000.0, 2: 30000.0, 3: 8000.0})
    end_target_penalty_rate = float(cost_params.get("end_target_shortfall_penalty_per_kwh", 5.0))

    charging = plan.charging
    assignments = plan.assignments
    soc = plan.soc
    site_load = plan.site_load

    # 1. Energy & Electricity Cost
    if not charging.empty and "power_kw_grid" in charging.columns:
        valid_chg = charging[charging["power_kw_grid"] > 1e-4].copy()
        if not valid_chg.empty:
            valid_chg["kwh_grid"] = valid_chg["power_kw_grid"] * slot_hours
            if "price_per_kwh" not in valid_chg.columns or valid_chg["price_per_kwh"].isna().any():
                valid_chg["price_per_kwh"] = valid_chg["slot"].map(prices_df["price_per_kwh"])
            valid_chg["cost"] = valid_chg["kwh_grid"] * valid_chg["price_per_kwh"]

            energy_kwh_grid = float(valid_chg["kwh_grid"].sum())
            energy_cost = float(valid_chg["cost"].sum())
        else:
            energy_kwh_grid = 0.0
            energy_cost = 0.0
    else:
        energy_kwh_grid = 0.0
        energy_cost = 0.0

    # 2. Demand Cost
    if not site_load.empty and "site_kw" in site_load.columns:
        peak_kw = float(site_load["site_kw"].max())
    elif not charging.empty:
        site_by_slot = charging.groupby("slot")["power_kw_grid"].sum()
        peak_kw = float(site_by_slot.max()) if not site_by_slot.empty else 0.0
    else:
        peak_kw = 0.0

    demand_cost = peak_kw * demand_charge

    # 3. Battery Wear Cost
    # Throughput wear + high SOC dwell wear
    vehicles_df = opt_input.vehicles.set_index("vehicle_id")
    throughput_wear = 0.0
    if not charging.empty and not valid_chg.empty:
        for v_id, v_chgs in valid_chg.groupby("vehicle_id"):
            if v_id in vehicles_df.index:
                soh = float(vehicles_df.loc[v_id].get("soh", 1.0))
                v_wear_rate = wear_base * (1.0 / max(0.1, soh))
            else:
                v_wear_rate = wear_base
            e_bat = v_chgs["energy_to_battery_kwh"].sum() if "energy_to_battery_kwh" in v_chgs.columns else v_chgs["kwh_grid"].sum() * 0.92
            throughput_wear += float(e_bat * v_wear_rate)

    high_soc_wear = 0.0
    high_soc_vehicle_hours = 0.0
    if not soc.empty:
        for v_id, v_soc in soc.groupby("vehicle_id"):
            if v_id in vehicles_df.index:
                e_high = float(vehicles_df.loc[v_id].get("e_high", 0.8 * float(vehicles_df.loc[v_id]["usable_capacity_kwh"])))
            else:
                e_high = 0.0
            above_h = (v_soc["energy_kwh"] - e_high).clip(lower=0.0)
            high_soc_wear += float((above_h * wear_high_rate * slot_hours).sum())
            high_soc_vehicle_hours += float((above_h > 1e-3).sum() * slot_hours)

    wear_cost = throughput_wear + high_soc_wear

    # 4. Trip Penalties
    trips_df = opt_input.trips.set_index("trip_id")
    trips_total = len(trips_df)
    if not assignments.empty:
        served_mask = assignments["served"] == True
        trips_served = int(served_mask.sum())
        trips_unserved = trips_total - trips_served

        unserved_penalty = 0.0
        unserved_trips = assignments[assignments["served"] == False]
        for _, u in unserved_trips.iterrows():
            t_id = u["trip_id"]
            pri = int(trips_df.loc[t_id]["priority"]) if t_id in trips_df.index else 2
            unserved_penalty += float(penalties.get(pri, 30000.0))
    else:
        trips_served = 0
        trips_unserved = trips_total
        unserved_penalty = sum(float(penalties.get(int(r["priority"]), 30000.0)) for _, r in trips_df.iterrows())

    # 5. End of Horizon Target Shortfall
    end_shortfall_kwh = 0.0
    if not soc.empty:
        last_slot = soc["slot"].max()
        final_soc = soc[soc["slot"] == last_slot]
        for _, r in final_soc.iterrows():
            v_id = r["vehicle_id"]
            if v_id in vehicles_df.index:
                target_e = float(vehicles_df.loc[v_id].get("e_end_target", 0.0))
                shortfall = max(0.0, target_e - float(r["energy_kwh"]))
                end_shortfall_kwh += shortfall

    end_shortfall_penalty = end_shortfall_kwh * end_target_penalty_rate

    # 6. Aggregates & Operating Cost
    operating_cost = energy_cost + demand_cost + wear_cost
    total_cost = operating_cost + unserved_penalty + end_shortfall_penalty
    avg_price_paid = (energy_cost / energy_kwh_grid) if energy_kwh_grid > 0 else 0.0

    # 7. Departure Margins
    min_departure_margin_kwh = 0.0
    if not assignments.empty and trips_served > 0:
        margins = []
        for _, ass in assignments[assignments["served"] == True].iterrows():
            t_id = ass["trip_id"]
            v_id = ass["vehicle_id"]
            if pd.isna(v_id) or not v_id or t_id not in trips_df.index:
                continue
            dep_s = int(trips_df.loc[t_id]["departure_slot"])
            # lookup vehicle SOC at departure slot
            v_dep_soc = soc[(soc["vehicle_id"] == v_id) & (soc["slot"] == dep_s)]
            if not v_dep_soc.empty:
                dep_e = float(v_dep_soc.iloc[0]["energy_kwh"])
                # lookup required departure energy
                m = opt_input.eligibility[(opt_input.eligibility["vehicle_id"] == v_id) & (opt_input.eligibility["trip_id"] == t_id)]
                if not m.empty:
                    req_e = float(m.iloc[0]["required_departure_kwh"])
                    margins.append(dep_e - req_e)
        if margins:
            min_departure_margin_kwh = float(min(margins))

    # 8. Charger Utilization by Type
    n_slots = opt_input.n_slots
    ac_cap_total = opt_input.charger_capacity[opt_input.charger_capacity["type"] == "AC"]["available_count"].sum()
    dc_cap_total = opt_input.charger_capacity[opt_input.charger_capacity["type"] == "DC"]["available_count"].sum()

    if not charging.empty:
        ac_occupied_slots = len(charging[(charging["charger_type"] == "AC") & (charging["power_kw_grid"] > 1e-4)])
        dc_occupied_slots = len(charging[(charging["charger_type"] == "DC") & (charging["power_kw_grid"] > 1e-4)])
    else:
        ac_occupied_slots = 0
        dc_occupied_slots = 0

    ac_util = (ac_occupied_slots / ac_cap_total) if ac_cap_total > 0 else 0.0
    dc_util = (dc_occupied_slots / dc_cap_total) if dc_cap_total > 0 else 0.0

    # 9. Energy by Tariff Period
    energy_by_period = {"offpeak": 0.0, "shoulder": 0.0, "peak": 0.0}
    if not charging.empty and not valid_chg.empty:
        if "period" not in valid_chg.columns:
            valid_chg["period"] = valid_chg["slot"].map(prices_df["period"])
        for p, group in valid_chg.groupby("period"):
            if p in energy_by_period:
                energy_by_period[p] = float(group["kwh_grid"].sum())

    peak_period_share = (energy_by_period["peak"] / energy_kwh_grid * 100.0) if energy_kwh_grid > 0 else 0.0

    return {
        "energy_kwh_grid": round(energy_kwh_grid, 2),
        "energy_cost": round(energy_cost, 2),
        "demand_cost": round(demand_cost, 2),
        "wear_cost": round(wear_cost, 2),
        "unserved_penalty": round(unserved_penalty, 2),
        "end_shortfall_kwh": round(end_shortfall_kwh, 2),
        "end_shortfall_penalty": round(end_shortfall_penalty, 2),
        "operating_cost": round(operating_cost, 2),
        "total_cost": round(total_cost, 2),
        "peak_kw": round(peak_kw, 2),
        "avg_price_paid": round(avg_price_paid, 3),
        "trips_total": trips_total,
        "trips_served": trips_served,
        "trips_unserved": trips_unserved,
        "min_departure_margin_kwh": round(min_departure_margin_kwh, 2),
        "charger_utilization": {
            "AC": round(ac_util, 3),
            "DC": round(dc_util, 3),
        },
        "energy_by_period_kwh": {
            "offpeak": round(energy_by_period["offpeak"], 2),
            "shoulder": round(energy_by_period["shoulder"], 2),
            "peak": round(energy_by_period["peak"], 2),
        },
        "peak_period_share_pct": round(peak_period_share, 1),
        "high_soc_vehicle_hours": round(high_soc_vehicle_hours, 1),
    }


def compute_savings_kpis(baseline_eval: dict[str, Any], optimized_eval: dict[str, Any], solver_info: dict[str, Any]) -> dict[str, Any]:
    """Computes honest baseline vs optimized savings using the exact same evaluation metrics."""
    base_op = baseline_eval["operating_cost"]
    opt_op = optimized_eval["operating_cost"]
    savings_abs = base_op - opt_op
    savings_pct = (savings_abs / base_op * 100.0) if base_op > 0 else 0.0

    energy_abs = baseline_eval["energy_cost"] - optimized_eval["energy_cost"]
    demand_abs = baseline_eval["demand_cost"] - optimized_eval["demand_cost"]
    wear_abs = baseline_eval["wear_cost"] - optimized_eval["wear_cost"]

    kwh_shifted = max(0.0, baseline_eval["energy_by_period_kwh"]["peak"] - optimized_eval["energy_by_period_kwh"]["peak"])

    return {
        "baseline": baseline_eval,
        "optimized": optimized_eval,
        "savings": {
            "abs": round(savings_abs, 2),
            "pct": round(savings_pct, 1),
            "energy_cost_abs": round(energy_abs, 2),
            "demand_cost_abs": round(demand_abs, 2),
            "wear_cost_abs": round(wear_abs, 2),
            "kwh_shifted_out_of_peak": round(kwh_shifted, 2),
        },
        "solver": solver_info,
    }
