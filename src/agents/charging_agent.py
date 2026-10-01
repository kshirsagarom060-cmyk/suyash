"""Charging Agent: models charger capacity by slot, outage impact, vehicle compatibility, and site limits."""

from __future__ import annotations

import pandas as pd

from src.agents.base import BaseAgent
from src.common.schemas import AgentContext, AgentResult


class ChargingAgent(BaseAgent):
    name = "charging_agent"

    def _execute(self, ctx: AgentContext) -> AgentResult:
        chargers = ctx.tables["chargers"]
        config = ctx.config
        n_slots = config.horizon.n_slots
        outages_df = ctx.tables.get("outages", pd.DataFrame())

        battery_res = ctx.results.get("battery_agent")
        battery_outputs = battery_res.outputs if isinstance(battery_res, AgentResult) else (battery_res.get("outputs", {}) if isinstance(battery_res, dict) else {})
        battery_table = battery_outputs.get("battery_table")
        if battery_table is None:
            battery_table = ctx.tables["vehicles"]

        route_res = ctx.results.get("route_agent")
        route_outputs = route_res.outputs if isinstance(route_res, AgentResult) else (route_res.get("outputs", {}) if isinstance(route_res, dict) else {})
        trip_energy_summary = route_outputs.get("trip_energy_summary", pd.DataFrame())

        warnings = []

        # 1. Charger capacity by (slot, type)
        ac_chargers = chargers[chargers["type"] == "AC"]
        dc_chargers = chargers[chargers["type"] == "DC"]

        ac_max_kw = float(ac_chargers["max_kw"].iloc[0]) if not ac_chargers.empty else 11.0
        dc_max_kw = float(dc_chargers["max_kw"].iloc[0]) if not dc_chargers.empty else 30.0
        ac_eff_kw = ac_max_kw * float(config.chargers.ac.derate_factor)
        dc_eff_kw = dc_max_kw * float(config.chargers.dc.derate_factor)

        capacity_rows = []
        for s in range(n_slots):
            # Outage checks
            ac_out = 0
            dc_out = 0
            if not outages_df.empty:
                for _, out in outages_df.iterrows():
                    if int(out["start_slot"]) <= s < int(out["end_slot"]):
                        c_id = str(out["charger_id"])
                        match_ch = chargers[chargers["charger_id"] == c_id]
                        if not match_ch.empty:
                            ch_type = match_ch.iloc[0]["type"]
                            if ch_type == "AC":
                                ac_out += 1
                            elif ch_type == "DC":
                                dc_out += 1

            avail_ac = max(0, len(ac_chargers) - ac_out)
            avail_dc = max(0, len(dc_chargers) - dc_out)

            capacity_rows.append({
                "slot": s,
                "type": "AC",
                "available_count": avail_ac,
                "effective_kw": ac_eff_kw,
                "total_kw": avail_ac * ac_eff_kw,
            })
            capacity_rows.append({
                "slot": s,
                "type": "DC",
                "available_count": avail_dc,
                "effective_kw": dc_eff_kw,
                "total_kw": avail_dc * dc_eff_kw,
            })

        charger_capacity = pd.DataFrame(capacity_rows)

        # 2. Site capacity by slot
        site_limit_kw = float(config.site.limit_kw)
        site_capacity = pd.DataFrame([
            {"slot": s, "site_limit_kw": site_limit_kw} for s in range(n_slots)
        ])

        # 3. Vehicle compatibility matrix
        compat_rows = []
        for _, veh in battery_table.iterrows():
            v_id = veh["vehicle_id"]
            max_ac = float(veh["max_ac_kw"])
            max_dc = float(veh["max_dc_kw"])

            eff_ac = min(ac_eff_kw, max_ac)
            eff_dc = min(dc_eff_kw, max_dc) if max_dc > 0 else 0.0

            compat_rows.append({
                "vehicle_id": v_id,
                "type": "AC",
                "max_kw": round(eff_ac, 2),
                "compatible": eff_ac > 0,
            })
            compat_rows.append({
                "vehicle_id": v_id,
                "type": "DC",
                "max_kw": round(eff_dc, 2),
                "compatible": eff_dc > 0,
            })

        compatibility = pd.DataFrame(compat_rows)

        # 4. Total fleet energy demand vs deliverable capacity check
        total_trip_energy = float(trip_energy_summary["min_energy_kwh"].sum()) if not trip_energy_summary.empty else 0.0
        fleet_initial_energy = float(battery_table["e_init"].sum())
        fleet_target_energy = float(battery_table["e_end_target"].sum())
        net_energy_needed = max(0.0, fleet_target_energy - fleet_initial_energy) + total_trip_energy

        slot_hours = config.horizon.slot_hours
        total_deliverable_energy = (charger_capacity.groupby("slot")["total_kw"].sum() * slot_hours).sum()
        site_max_deliverable_energy = site_limit_kw * slot_hours * n_slots

        capacity_summary = {
            "net_energy_needed_kwh": round(net_energy_needed, 2),
            "total_charger_potential_kwh": round(total_deliverable_energy, 2),
            "site_max_deliverable_kwh": round(site_max_deliverable_energy, 2),
            "site_limit_kw": site_limit_kw,
            "ac_chargers_count": len(ac_chargers),
            "dc_chargers_count": len(dc_chargers),
        }

        if net_energy_needed > site_max_deliverable_energy:
            warnings.append(
                f"Total net energy needed ({net_energy_needed:.1f} kWh) exceeds site capacity limit ({site_max_deliverable_energy:.1f} kWh)."
            )

        status = "warning" if warnings else "ok"
        outputs = {
            "charger_capacity": charger_capacity,
            "site_capacity": site_capacity,
            "compatibility": compatibility,
            "capacity_summary": capacity_summary,
        }

        return AgentResult(
            agent=self.name,
            status=status,
            outputs=outputs,
            warnings=warnings,
            metrics={
                "site_limit_kw": site_limit_kw,
                "net_energy_needed_kwh": float(net_energy_needed),
                "site_max_deliverable_kwh": float(site_max_deliverable_energy),
            },
        )
