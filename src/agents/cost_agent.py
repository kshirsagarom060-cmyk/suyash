"""Cost Agent: formats price vectors, economic parameters, and price window insights."""

from __future__ import annotations

import pandas as pd

from src.agents.base import BaseAgent
from src.common.schemas import AgentContext, AgentResult


class CostAgent(BaseAgent):
    name = "cost_agent"

    def _execute(self, ctx: AgentContext) -> AgentResult:
        tariffs = ctx.tables["tariffs"]
        config = ctx.config
        depots = ctx.tables["depots"]
        depot_id = depots["depot_id"].iloc[0] if not depots.empty else "DEPOT-01"

        depot_prices = tariffs[tariffs["depot_id"] == depot_id].sort_values("slot").copy()
        if depot_prices.empty:
            depot_prices = tariffs.sort_values("slot").copy()

        cost_params = {
            "demand_charge_per_kw": float(config.tariff.demand_charge_per_kw),
            "wear_cost_per_kwh_throughput": float(config.costs.wear_cost_per_kwh_throughput),
            "wear_cost_per_kwh_slot_above_threshold": float(config.costs.wear_cost_per_kwh_slot_above_threshold),
            "unserved_trip_penalty": config.costs.unserved_trip_penalty,
            "end_target_shortfall_penalty_per_kwh": float(config.costs.end_target_shortfall_penalty_per_kwh),
            "weights": {
                "unavailability": float(config.weights.unavailability),
                "battery_wear": float(config.weights.battery_wear),
                "violations": float(config.weights.violations),
            },
            "currency": config.currency,
        }

        # Compute price insights
        price_series = depot_prices["price_per_kwh"].values
        min_p = float(price_series.min()) if len(price_series) > 0 else 0.0
        max_p = float(price_series.max()) if len(price_series) > 0 else 0.0
        avg_p = float(price_series.mean()) if len(price_series) > 0 else 0.0
        price_spread = max_p - min_p

        # Find cheapest 4-hour window (16 slots of 15 min)
        window_size = min(16, len(price_series))
        cheapest_start_slot = 0
        min_window_sum = float("inf")
        if window_size > 0:
            for s in range(len(price_series) - window_size + 1):
                w_sum = sum(price_series[s:s+window_size])
                if w_sum < min_window_sum:
                    min_window_sum = w_sum
                    cheapest_start_slot = s

        peak_slots = depot_prices[depot_prices["period"] == "peak"]["slot"].tolist()
        offpeak_slots = depot_prices[depot_prices["period"] == "offpeak"]["slot"].tolist()

        price_insights = {
            "min_price": round(min_p, 3),
            "max_price": round(max_p, 3),
            "avg_price": round(avg_p, 3),
            "price_spread": round(price_spread, 3),
            "cheapest_4h_start_slot": cheapest_start_slot,
            "peak_slots_count": len(peak_slots),
            "offpeak_slots_count": len(offpeak_slots),
        }

        outputs = {
            "prices": depot_prices,
            "cost_params": cost_params,
            "price_insights": price_insights,
        }

        return AgentResult(
            agent=self.name,
            status="ok",
            outputs=outputs,
            warnings=[],
            metrics={
                "min_price": min_p,
                "max_price": max_p,
                "price_spread": price_spread,
            },
        )
