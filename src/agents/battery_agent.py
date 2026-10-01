"""Battery Agent: calculates usable capacity, energy thresholds, and battery wear costs."""

from __future__ import annotations

import pandas as pd

from src.agents.base import BaseAgent
from src.common.schemas import AgentContext, AgentResult


class BatteryAgent(BaseAgent):
    name = "battery_agent"

    def _execute(self, ctx: AgentContext) -> AgentResult:
        fleet_res = ctx.results.get("fleet_agent")
        usable_vehicles = None
        if isinstance(fleet_res, AgentResult):
            usable_vehicles = fleet_res.outputs.get("usable_vehicles")
        elif isinstance(fleet_res, dict):
            usable_vehicles = fleet_res.get("outputs", {}).get("usable_vehicles")

        if usable_vehicles is None or usable_vehicles.empty:
            usable_vehicles = ctx.tables["vehicles"][ctx.tables["vehicles"]["status"] == "available"].copy()

        config = ctx.config
        battery_cfg = config.battery
        costs_cfg = config.costs

        rows = []
        alerts = []
        warnings = []

        for _, veh in usable_vehicles.iterrows():
            v_id = veh["vehicle_id"]
            cap = float(veh["battery_capacity_kwh"])
            soh = float(veh["soh"])
            soc_pct = float(veh["current_soc_pct"])
            res_pct = float(veh["reserve_soc_pct"])
            ceil_pct = float(veh["ceiling_soc_pct"])
            high_pct = float(battery_cfg.high_soc_threshold_pct)
            end_pct = float(battery_cfg.end_of_horizon_target_pct)

            usable_cap = cap * soh
            e_init = (soc_pct / 100.0) * usable_cap
            e_min = (res_pct / 100.0) * usable_cap
            e_max = (ceil_pct / 100.0) * usable_cap
            e_high = (high_pct / 100.0) * usable_cap
            e_end_target = min(e_max, (end_pct / 100.0) * usable_cap)

            # Wear cost scaled by 1/soh
            wear_cost = float(costs_cfg.wear_cost_per_kwh_throughput * (1.0 / soh))

            rows.append({
                "vehicle_id": v_id,
                "model": veh["model"],
                "depot_id": veh["depot_id"],
                "battery_capacity_kwh": cap,
                "soh": soh,
                "usable_capacity_kwh": round(usable_cap, 3),
                "current_soc_pct": soc_pct,
                "e_init": round(e_init, 3),
                "e_min": round(e_min, 3),
                "e_max": round(e_max, 3),
                "e_high": round(e_high, 3),
                "e_end_target": round(e_end_target, 3),
                "wear_cost_per_kwh": round(wear_cost, 4),
                "max_ac_kw": float(veh["max_ac_kw"]),
                "max_dc_kw": float(veh["max_dc_kw"]),
                "available_from_slot": int(veh["available_from_slot"]),
            })

            # Check alerts
            if soc_pct < res_pct:
                alerts.append({
                    "vehicle_id": v_id,
                    "type": "low_soc",
                    "message": f"Vehicle {v_id} initial SOC ({soc_pct:.1f}%) is below reserve ({res_pct:.1f}%).",
                    "severity": "warning",
                })
                warnings.append(f"Vehicle {v_id} initial SOC is below reserve threshold.")

            if soh < 0.80:
                alerts.append({
                    "vehicle_id": v_id,
                    "type": "degraded_soh",
                    "message": f"Vehicle {v_id} state of health ({soh:.2f}) is below 0.80.",
                    "severity": "warning",
                })

        battery_table = pd.DataFrame(rows)
        status = "warning" if warnings else "ok"

        outputs = {
            "battery_table": battery_table,
            "battery_alerts": alerts,
        }

        return AgentResult(
            agent=self.name,
            status=status,
            outputs=outputs,
            warnings=warnings,
            metrics={
                "total_battery_capacity_kwh": float(battery_table["usable_capacity_kwh"].sum()),
                "avg_soh": float(battery_table["soh"].mean()) if not battery_table.empty else 0.0,
                "n_low_soc_alerts": float(len([a for a in alerts if a["type"] == "low_soc"])),
            },
        )
