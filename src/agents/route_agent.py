"""Route Agent: computes trip energy requirements and checks vehicle range feasibility."""

from __future__ import annotations

import pandas as pd

from src.agents.base import BaseAgent
from src.common.schemas import AgentContext, AgentResult


class RouteAgent(BaseAgent):
    name = "route_agent"

    @staticmethod
    def calc_temp_factor(temp_c: float) -> float:
        """Calculates temperature penalty multiplier for HVAC energy draw."""
        cold_penalty = 0.012 * max(0.0, 18.0 - temp_c)
        hot_penalty = 0.008 * max(0.0, temp_c - 28.0)
        return 1.0 + cold_penalty + hot_penalty

    @staticmethod
    def calc_payload_factor(trip_payload_kg: float, veh_payload_capacity_kg: float) -> float:
        """Calculates payload penalty multiplier."""
        if veh_payload_capacity_kg <= 0:
            return 1.0
        return 1.0 + 0.15 * (trip_payload_kg / veh_payload_capacity_kg)

    def _execute(self, ctx: AgentContext) -> AgentResult:
        trips = ctx.tables["trips"]
        config = ctx.config
        safety_margin = float(config.trips.safety_margin)

        fleet_res = ctx.results.get("fleet_agent")
        battery_res = ctx.results.get("battery_agent")

        fleet_outputs = fleet_res.outputs if isinstance(fleet_res, AgentResult) else (fleet_res.get("outputs", {}) if isinstance(fleet_res, dict) else {})
        battery_outputs = battery_res.outputs if isinstance(battery_res, AgentResult) else (battery_res.get("outputs", {}) if isinstance(battery_res, dict) else {})

        eligibility_base = fleet_outputs.get("eligibility_base")
        battery_table = battery_outputs.get("battery_table")
        usable_vehicles = fleet_outputs.get("usable_vehicles")

        if battery_table is None:
            raise ValueError("Battery table missing from BatteryAgent output.")

        veh_lookup = {r["vehicle_id"]: r for _, r in battery_table.iterrows()}
        veh_specs = {r["vehicle_id"]: r for _, r in usable_vehicles.iterrows()}
        trip_lookup = {r["trip_id"]: r for _, r in trips.iterrows()}

        elig_rows = []
        trip_energy_summaries = []
        warnings = []

        # Merge eligibility base with energy and range constraints
        for _, trip in trips.iterrows():
            t_id = trip["trip_id"]
            dist_km = float(trip["distance_km"])
            p_kg = float(trip["payload_kg"])
            temp_c = float(trip["temperature_c"])
            temp_fac = self.calc_temp_factor(temp_c)

            min_req_energy = float("inf")
            eligible_veh_count = 0

            for v_id, b_row in veh_lookup.items():
                v_spec = veh_specs[v_id]
                # Check base eligibility from fleet agent
                base_match = eligibility_base[
                    (eligibility_base["vehicle_id"] == v_id) & (eligibility_base["trip_id"] == t_id)
                ]
                base_eligible = bool(base_match.iloc[0]["eligible"]) if not base_match.empty else False
                base_reason = str(base_match.iloc[0]["reason"]) if not base_match.empty else "depot"

                payload_fac = self.calc_payload_factor(p_kg, float(v_spec["payload_capacity_kg"]))
                eff = float(v_spec["efficiency_kwh_per_km"])

                energy_kwh = dist_km * eff * temp_fac * payload_fac * (1.0 + safety_margin)
                req_dep_kwh = energy_kwh + float(b_row["e_min"])

                e_max = float(b_row["e_max"])

                if not base_eligible:
                    is_elig = False
                    reason = base_reason
                elif req_dep_kwh > e_max:
                    is_elig = False
                    reason = "range"
                else:
                    is_elig = True
                    reason = "ok"
                    eligible_veh_count += 1
                    min_req_energy = min(min_req_energy, energy_kwh)

                elig_rows.append({
                    "vehicle_id": v_id,
                    "trip_id": t_id,
                    "eligible": is_elig,
                    "energy_kwh": round(energy_kwh, 3),
                    "required_departure_kwh": round(req_dep_kwh, 3),
                    "reason": reason,
                })

            trip_energy_summaries.append({
                "trip_id": t_id,
                "distance_km": dist_km,
                "min_energy_kwh": round(min_req_energy if min_req_energy < float("inf") else 0.0, 3),
                "eligible_vehicles_count": eligible_veh_count,
            })

            if eligible_veh_count == 0:
                warnings.append(f"Trip {t_id} has zero eligible vehicles after energy and range checks.")

        eligibility = pd.DataFrame(elig_rows)
        trip_energy_summary = pd.DataFrame(trip_energy_summaries)

        status = "warning" if warnings else "ok"
        outputs = {
            "eligibility": eligibility,
            "trip_energy_summary": trip_energy_summary,
        }

        return AgentResult(
            agent=self.name,
            status=status,
            outputs=outputs,
            warnings=warnings,
            metrics={
                "total_trip_energy_kwh": float(trip_energy_summary["min_energy_kwh"].sum()),
                "avg_eligible_vehicles_per_trip": float(trip_energy_summary["eligible_vehicles_count"].mean()),
            },
        )
