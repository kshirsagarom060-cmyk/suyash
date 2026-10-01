"""Fleet Agent: tracks vehicle inventory, availability, and trip eligibility skeleton."""

from __future__ import annotations

import pandas as pd

from src.agents.base import BaseAgent
from src.common.schemas import AgentContext, AgentResult
from src.common.time_grid import get_plan_start_datetime, slot_to_datetime


class FleetAgent(BaseAgent):
    name = "fleet_agent"

    def _execute(self, ctx: AgentContext) -> AgentResult:
        vehicles = ctx.tables["vehicles"]
        trips = ctx.tables["trips"]
        config = ctx.config
        warnings: list[str] = []

        # 1. Usable vehicles
        usable_mask = vehicles["status"] == "available"
        usable_vehicles = vehicles[usable_mask].copy()
        n_usable = len(usable_vehicles)
        n_total = len(vehicles)
        n_maint = len(vehicles[vehicles["status"] == "maintenance"])

        # 2. Build eligibility base skeleton (depot match and payload capacity)
        elig_rows = []
        for _, trip in trips.iterrows():
            trip_id = trip["trip_id"]
            depot_id = trip["depot_id"]
            payload = trip["payload_kg"]

            trip_eligible_count = 0
            for _, veh in usable_vehicles.iterrows():
                v_id = veh["vehicle_id"]
                v_depot = veh["depot_id"]
                v_payload = veh["payload_capacity_kg"]

                if v_depot != depot_id:
                    elig = False
                    reason = "depot"
                elif payload > v_payload:
                    elig = False
                    reason = "payload"
                else:
                    elig = True
                    reason = "ok"
                    trip_eligible_count += 1

                elig_rows.append({
                    "vehicle_id": v_id,
                    "trip_id": trip_id,
                    "eligible": elig,
                    "reason": reason,
                })

            if trip_eligible_count == 0:
                warnings.append(f"Trip {trip_id} has zero eligible vehicles based on depot and payload.")

        eligibility_base = pd.DataFrame(elig_rows)

        # 3. Time overlap / concurrent demand analysis
        n_slots = config.horizon.n_slots
        concurrent_trips_per_slot = [0] * n_slots
        for _, trip in trips.iterrows():
            dep = int(trip["departure_slot"])
            ret = int(trip["return_slot"])
            for s in range(dep, min(ret, n_slots)):
                concurrent_trips_per_slot[s] += 1

        peak_concurrent_trips = max(concurrent_trips_per_slot) if concurrent_trips_per_slot else 0
        if peak_concurrent_trips > n_usable:
            warnings.append(
                f"Peak concurrent trip demand ({peak_concurrent_trips}) exceeds usable vehicle count ({n_usable})."
            )

        # 4. Demand summary by hour
        start_dt = get_plan_start_datetime(config.horizon.plan_date, config.horizon.start_time)
        hour_demand = {}
        for _, trip in trips.iterrows():
            dep_dt = slot_to_datetime(int(trip["departure_slot"]), start_dt, config.horizon.slot_minutes)
            hr = dep_dt.hour
            if hr not in hour_demand:
                hour_demand[hr] = {"hour": hr, "count": 0, "distance_km": 0.0, "p1": 0, "p2": 0, "p3": 0}
            hour_demand[hr]["count"] += 1
            hour_demand[hr]["distance_km"] += float(trip["distance_km"])
            pri = int(trip["priority"])
            if pri == 1:
                hour_demand[hr]["p1"] += 1
            elif pri == 2:
                hour_demand[hr]["p2"] += 1
            else:
                hour_demand[hr]["p3"] += 1

        demand_by_hour = pd.DataFrame(list(hour_demand.values())).sort_values("hour") if hour_demand else pd.DataFrame()

        # 5. Fleet summary metrics
        pri_counts = trips["priority"].value_counts().to_dict()
        fleet_summary = {
            "total_vehicles": n_total,
            "usable_vehicles": n_usable,
            "maintenance_vehicles": n_maint,
            "total_trips": len(trips),
            "priority_1_trips": int(pri_counts.get(1, 0)),
            "priority_2_trips": int(pri_counts.get(2, 0)),
            "priority_3_trips": int(pri_counts.get(3, 0)),
            "peak_concurrent_demand": peak_concurrent_trips,
        }

        status = "warning" if warnings else "ok"
        outputs = {
            "usable_vehicles": usable_vehicles,
            "eligibility_base": eligibility_base,
            "demand_by_hour": demand_by_hour,
            "fleet_summary": fleet_summary,
            "concurrent_trips_per_slot": concurrent_trips_per_slot,
        }

        return AgentResult(
            agent=self.name,
            status=status,
            outputs=outputs,
            warnings=warnings,
            metrics={
                "n_usable_vehicles": float(n_usable),
                "n_trips": float(len(trips)),
                "peak_concurrent_demand": float(peak_concurrent_trips),
            },
        )
