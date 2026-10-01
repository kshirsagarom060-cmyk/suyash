"""Optimization Agent: assembles inputs, coordinates baseline vs solver execution, and computes evaluations."""

from __future__ import annotations

import pandas as pd

from src.agents.base import BaseAgent
from src.common.schemas import AgentContext, AgentResult, OptimizationInput
from src.optimization.baseline import build_baseline_plan
from src.optimization.solver import solve
from src.optimization.validator import validate_plan
from src.optimization.evaluate import evaluate_plan, compute_savings_kpis


class OptimizationAgent(BaseAgent):
    name = "optimization_agent"

    def _execute(self, ctx: AgentContext) -> AgentResult:
        config = ctx.config
        trips = ctx.tables["trips"]
        chargers = ctx.tables["chargers"]

        battery_res = ctx.results.get("battery_agent")
        battery_outputs = battery_res.outputs if isinstance(battery_res, AgentResult) else (battery_res.get("outputs", {}) if isinstance(battery_res, dict) else {})
        battery_table = battery_outputs.get("battery_table")

        route_res = ctx.results.get("route_agent")
        route_outputs = route_res.outputs if isinstance(route_res, AgentResult) else (route_res.get("outputs", {}) if isinstance(route_res, dict) else {})
        eligibility = route_outputs.get("eligibility")

        charging_res = ctx.results.get("charging_agent")
        charging_outputs = charging_res.outputs if isinstance(charging_res, AgentResult) else (charging_res.get("outputs", {}) if isinstance(charging_res, dict) else {})
        charger_capacity = charging_outputs.get("charger_capacity")
        site_capacity = charging_outputs.get("site_capacity")

        cost_res = ctx.results.get("cost_agent")
        cost_outputs = cost_res.outputs if isinstance(cost_res, AgentResult) else (cost_res.get("outputs", {}) if isinstance(cost_res, dict) else {})
        prices = cost_outputs.get("prices")
        cost_params = cost_outputs.get("cost_params")

        # Assemble OptimizationInput
        opt_input = OptimizationInput(
            vehicles=battery_table,
            trips=trips,
            eligibility=eligibility,
            chargers=chargers,
            charger_capacity=charger_capacity,
            site_capacity=site_capacity,
            prices=prices,
            cost_params=cost_params,
            n_slots=config.horizon.n_slots,
            slot_hours=config.horizon.slot_hours,
        )

        # 1. Run Baseline Plan
        baseline_plan = build_baseline_plan(opt_input, config)
        baseline_violations = validate_plan(baseline_plan, opt_input, config)
        baseline_eval = evaluate_plan(baseline_plan, opt_input, config)

        # 2. Run Optimized Plan
        optimized_plan = solve(opt_input, config)
        optimized_violations = validate_plan(optimized_plan, opt_input, config)
        optimized_eval = evaluate_plan(optimized_plan, opt_input, config)

        # 3. Compute Savings KPIs
        kpis = compute_savings_kpis(baseline_eval, optimized_eval, optimized_plan.solver_info)

        warnings = []
        if optimized_violations:
            warnings.append(f"Optimized plan has {len(optimized_violations)} constraint violations.")
        if baseline_violations:
            warnings.append(f"Baseline plan has {len(baseline_violations)} constraint violations.")

        status = "warning" if optimized_violations else "ok"

        outputs = {
            "opt_input": opt_input,
            "baseline_plan": baseline_plan,
            "optimized_plan": optimized_plan,
            "kpis": kpis,
            "violations": {
                "baseline": [v.__dict__ for v in baseline_violations],
                "optimized": [v.__dict__ for v in optimized_violations],
            },
        }

        return AgentResult(
            agent=self.name,
            status=status,
            outputs=outputs,
            warnings=warnings,
            metrics={
                "savings_abs": float(kpis["savings"]["abs"]),
                "savings_pct": float(kpis["savings"]["pct"]),
                "baseline_operating_cost": float(baseline_eval["operating_cost"]),
                "optimized_operating_cost": float(optimized_eval["operating_cost"]),
            },
        )
