"""Solver orchestrator: solves MILP model, falls back to heuristic if needed, and explains bottlenecks."""

from __future__ import annotations

import logging
from typing import Any
import pandas as pd

from src.common.config import Config
from src.common.schemas import OptimizationInput, Plan
from src.optimization.model import build_and_solve_milp
from src.optimization.heuristic import build_heuristic_plan
from src.optimization.validator import validate_plan

logger = logging.getLogger("ev_fleet_optimizer.solver")


def explain_infeasibility(opt_input: OptimizationInput) -> list[dict[str, Any]]:
    """Identifies root causes of potential infeasibilities or tight bottlenecks in input data."""
    explanations = []
    elig_df = opt_input.eligibility
    trips_df = opt_input.trips

    # 1. Trips with zero eligible vehicles
    for t_id, group in elig_df.groupby("trip_id"):
        if not group["eligible"].any():
            reasons = group["reason"].value_counts().to_dict()
            explanations.append({
                "type": "unserviceable_trip",
                "trip_id": t_id,
                "message": f"Trip {t_id} has zero eligible vehicles across the entire fleet.",
                "details": reasons,
            })

    # 2. Site capacity bottleneck
    total_energy_req = float(elig_df[elig_df["eligible"] == True].groupby("trip_id")["energy_kwh"].min().sum())
    site_max_kwh = float(opt_input.site_capacity["site_limit_kw"].sum() * opt_input.slot_hours)
    if total_energy_req > site_max_kwh:
        explanations.append({
            "type": "site_capacity_deficit",
            "message": f"Total trip energy ({total_energy_req:.1f} kWh) exceeds total site energy deliverable ({site_max_kwh:.1f} kWh).",
        })

    return explanations


def solve(opt_input: OptimizationInput, config: Config) -> Plan:
    """Executes the MILP solver and falls back seamlessly to the price-aware heuristic if needed."""
    logger.info("Attempting PuLP CBC MILP solve...")
    try:
        plan, solver_info = build_and_solve_milp(opt_input, config)
        if plan is not None:
            # Validate plan
            violations = validate_plan(plan, opt_input, config)
            # Filter fatal hard violations
            fatal_violations = [v for v in violations if v.type in ["double_booking", "charge_while_away", "incompatible_charger", "charger_overcapacity"]]
            if not fatal_violations:
                logger.info(f"MILP solver succeeded with status: {solver_info['status']}")
                return plan
            else:
                logger.warning(f"MILP solution contained {len(fatal_violations)} fatal violations. Engaging fallback heuristic...")
    except Exception as e:
        logger.error(f"MILP solver encountered exception: {e}. Engaging fallback heuristic...", exc_info=True)

    # Heuristic fallback
    logger.info("Running price-aware greedy heuristic fallback...")
    fallback_plan = build_heuristic_plan(opt_input, config)
    fallback_plan.solver_info["method"] = "heuristic_fallback"
    return fallback_plan
