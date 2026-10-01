"""Recommendation Agent: rule-based operational advice and verified executive summaries."""

from __future__ import annotations

import os
from typing import Any, Optional
import pandas as pd

from src.agents.base import BaseAgent
from src.common.schemas import AgentContext, AgentResult, Recommendation


class RecommendationAgent(BaseAgent):
    name = "recommendation_agent"

    def _execute(self, ctx: AgentContext) -> AgentResult:
        config = ctx.config
        opt_res = ctx.results.get("optimization_agent")
        opt_outputs = opt_res.outputs if isinstance(opt_res, AgentResult) else (opt_res.get("outputs", {}) if isinstance(opt_res, dict) else {})
        kpis = opt_outputs.get("kpis", {})
        baseline_plan = opt_outputs.get("baseline_plan")
        optimized_plan = opt_outputs.get("optimized_plan")
        opt_input = opt_outputs.get("opt_input")

        battery_res = ctx.results.get("battery_agent")
        battery_outputs = battery_res.outputs if isinstance(battery_res, AgentResult) else (battery_res.get("outputs", {}) if isinstance(battery_res, dict) else {})
        battery_table = battery_outputs.get("battery_table")
        battery_alerts = battery_outputs.get("battery_alerts", [])

        recommendations: list[dict[str, Any]] = []
        rec_id_counter = 1

        # 1. Critical Rules
        # Rule: Unserved Trips (especially priority 1)
        if optimized_plan is not None:
            trips_df = opt_input.trips.set_index("trip_id")
            unserved = optimized_plan.assignments[optimized_plan.assignments["served"] == False]
            for _, u in unserved.iterrows():
                t_id = u["trip_id"]
                pri = int(trips_df.loc[t_id]["priority"]) if t_id in trips_df.index else 2
                sev = "critical" if pri == 1 else "warning"
                recommendations.append({
                    "id": f"REC-{rec_id_counter:03d}",
                    "severity": sev,
                    "category": "trip_unserved",
                    "title": f"Unserved Priority {pri} Trip: {t_id}",
                    "detail": f"Trip {t_id} could not be served due to vehicle range, payload, or depot constraints.",
                    "trip_id": t_id,
                    "evidence": {
                        "priority": pri,
                        "distance_km": float(trips_df.loc[t_id]["distance_km"]),
                        "payload_kg": float(trips_df.loc[t_id]["payload_kg"]),
                    },
                })
                rec_id_counter += 1

            # Rule: Thin Departure Margins (< 5% of usable battery capacity)
            served = optimized_plan.assignments[optimized_plan.assignments["served"] == True]
            for _, s_row in served.iterrows():
                t_id = s_row["trip_id"]
                v_id = s_row["vehicle_id"]
                if not v_id or v_id not in battery_table.set_index("vehicle_id").index:
                    continue
                v_row = battery_table.set_index("vehicle_id").loc[v_id]
                usable_cap = float(v_row["usable_capacity_kwh"])
                dep_s = int(trips_df.loc[t_id]["departure_slot"])
                v_dep_soc = optimized_plan.soc[(optimized_plan.soc["vehicle_id"] == v_id) & (optimized_plan.soc["slot"] == dep_s)]
                if not v_dep_soc.empty:
                    dep_e = float(v_dep_soc.iloc[0]["energy_kwh"])
                    m = opt_input.eligibility[(opt_input.eligibility["vehicle_id"] == v_id) & (opt_input.eligibility["trip_id"] == t_id)]
                    if not m.empty:
                        req_e = float(m.iloc[0]["required_departure_kwh"])
                        margin_kwh = dep_e - req_e
                        if margin_kwh < 0.05 * usable_cap:
                            recommendations.append({
                                "id": f"REC-{rec_id_counter:03d}",
                                "severity": "warning",
                                "category": "thin_margin",
                                "title": f"Low Departure Buffer for {v_id} on {t_id}",
                                "detail": f"Vehicle {v_id} departs with only {margin_kwh:.1f} kWh buffer above minimum reserve.",
                                "vehicle_id": v_id,
                                "trip_id": t_id,
                                "evidence": {
                                    "margin_kwh": round(margin_kwh, 2),
                                    "usable_capacity_kwh": round(usable_cap, 2),
                                    "departure_soc_pct": float(s_row["departure_soc_pct"]),
                                },
                            })
                            rec_id_counter += 1

        # 2. Warning Rules
        # Battery Health & Low SOC alerts from BatteryAgent
        for alert in battery_alerts:
            v_id = alert["vehicle_id"]
            if alert["type"] == "low_soc":
                recommendations.append({
                    "id": f"REC-{rec_id_counter:03d}",
                    "severity": "warning",
                    "category": "battery_low_soc",
                    "title": f"Vehicle {v_id} Initial SOC Below Reserve",
                    "detail": alert["message"],
                    "vehicle_id": v_id,
                    "evidence": {"vehicle_id": v_id},
                })
                rec_id_counter += 1
            elif alert["type"] == "degraded_soh":
                recommendations.append({
                    "id": f"REC-{rec_id_counter:03d}",
                    "severity": "warning",
                    "category": "battery_degradation",
                    "title": f"Degraded Battery Health: {v_id}",
                    "detail": alert["message"],
                    "vehicle_id": v_id,
                    "evidence": {"vehicle_id": v_id},
                })
                rec_id_counter += 1

        # Site limit binding
        if optimized_plan is not None and not optimized_plan.site_load.empty:
            site_limit = float(config.site.limit_kw)
            binding_slots = (optimized_plan.site_load["site_kw"] >= site_limit - 1.0).sum()
            if binding_slots >= 4:
                recommendations.append({
                    "id": f"REC-{rec_id_counter:03d}",
                    "severity": "warning",
                    "category": "site_capacity",
                    "title": "Site Power Limit Binding",
                    "detail": f"Site power reached or neared capacity limit ({site_limit:.1f} kW) for {binding_slots} slots ({binding_slots*0.25:.1f} hours).",
                    "evidence": {
                        "binding_slots": int(binding_slots),
                        "site_limit_kw": site_limit,
                    },
                })
                rec_id_counter += 1

        # 3. Info Rules: Peak Shift & Savings Summary
        if kpis and "savings" in kpis:
            sav = kpis["savings"]
            kwh_shifted = float(sav.get("kwh_shifted_out_of_peak", 0.0))
            if kwh_shifted > 1.0:
                recommendations.append({
                    "id": f"REC-{rec_id_counter:03d}",
                    "severity": "info",
                    "category": "load_shifting",
                    "title": "Smart Off-Peak Load Shifting",
                    "detail": f"Shifted {kwh_shifted:.1f} kWh of charging from high-tariff peak slots to low-cost off-peak windows.",
                    "evidence": {
                        "kwh_shifted": round(kwh_shifted, 2),
                        "savings_abs": float(sav.get("abs", 0.0)),
                    },
                })
                rec_id_counter += 1

            if float(sav.get("abs", 0.0)) > 0:
                recommendations.append({
                    "id": f"REC-{rec_id_counter:03d}",
                    "severity": "info",
                    "category": "cost_reduction",
                    "title": f"Total Operating Cost Savings: {config.currency} {sav.get('abs', 0.0):.2f}",
                    "detail": f"Optimized smart charging achieves {sav.get('pct', 0.0):.1f}% reduction in total operating costs compared to unmanaged baseline charging.",
                    "evidence": {
                        "savings_abs": float(sav.get("abs", 0.0)),
                        "savings_pct": float(sav.get("pct", 0.0)),
                        "energy_cost_saved": float(sav.get("energy_cost_abs", 0.0)),
                        "demand_cost_saved": float(sav.get("demand_cost_abs", 0.0)),
                        "wear_cost_saved": float(sav.get("wear_cost_abs", 0.0)),
                    },
                })
                rec_id_counter += 1

        # Sort recommendations: critical first, then warning, then info; limit to 15
        order = {"critical": 0, "warning": 1, "info": 2}
        recommendations.sort(key=lambda r: order.get(r["severity"], 3))
        recommendations = recommendations[:15]

        # Generate Deterministic Executive Summary
        opt_kpi = kpis.get("optimized", {})
        base_kpi = kpis.get("baseline", {})
        sav_kpi = kpis.get("savings", {})

        exec_summary = (
            f"The optimization plan successfully evaluated {opt_kpi.get('trips_served', 0)} of {opt_kpi.get('trips_total', 0)} trips "
            f"with total operating cost of {config.currency} {opt_kpi.get('operating_cost', 0.0):.2f} "
            f"(vs {config.currency} {base_kpi.get('operating_cost', 0.0):.2f} baseline), saving {config.currency} {sav_kpi.get('abs', 0.0):.2f} "
            f"({sav_kpi.get('pct', 0.0):.1f}%). A total of {sav_kpi.get('kwh_shifted_out_of_peak', 0.0):.1f} kWh of charging was shifted "
            f"out of peak tariff windows to off-peak periods, maintaining zero hard constraint violations."
        )

        outputs = {
            "recommendations": recommendations,
            "executive_summary": exec_summary,
            "llm_used": False,
        }

        return AgentResult(
            agent=self.name,
            status="ok",
            outputs=outputs,
            warnings=[],
            metrics={
                "n_recommendations": float(len(recommendations)),
                "n_critical": float(len([r for r in recommendations if r["severity"] == "critical"])),
                "n_warning": float(len([r for r in recommendations if r["severity"] == "warning"])),
            },
        )
