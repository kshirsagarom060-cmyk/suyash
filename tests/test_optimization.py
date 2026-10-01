"""Tests for the MILP optimization model, baseline, heuristic fallback, evaluator, and validator."""

from __future__ import annotations

import copy
import pandas as pd
import pytest

from src.common.config import load_config
from src.common.schemas import AgentContext, OptimizationInput, Plan
from src.data.simulator import generate_all_tables
from src.data.scenarios import apply_scenario
from src.agents.fleet_agent import FleetAgent
from src.agents.battery_agent import BatteryAgent
from src.agents.route_agent import RouteAgent
from src.agents.charging_agent import ChargingAgent
from src.agents.cost_agent import CostAgent
from src.agents.optimization_agent import OptimizationAgent
from src.optimization.baseline import build_baseline_plan
from src.optimization.heuristic import build_heuristic_plan
from src.optimization.solver import solve
from src.optimization.validator import validate_plan
from src.optimization.evaluate import evaluate_plan


@pytest.fixture
def base_opt_input():
    """Small fixture with 4 vehicles, 3 trips, 1 AC + 1 DC charger, 48 slots."""
    config = load_config(overrides={
        "random_seed": 42,
        "fleet": {"n_vehicles": 4},
        "chargers": {
            "ac": {"count": 1, "power_kw": 11.0, "efficiency": 0.92, "derate_factor": 1.0},
            "dc": {"count": 1, "power_kw": 30.0, "efficiency": 0.94, "derate_factor": 0.85},
        },
        "horizon": {"n_slots": 48, "slot_minutes": 15},
        "solver": {"time_limit_s": 10, "mip_gap": 0.02, "threads": 1},
    })
    tables = generate_all_tables(config)
    ctx = AgentContext(config=config, tables=tables)

    ctx.results["fleet_agent"] = FleetAgent().run(ctx)
    ctx.results["battery_agent"] = BatteryAgent().run(ctx)
    ctx.results["route_agent"] = RouteAgent().run(ctx)
    ctx.results["charging_agent"] = ChargingAgent().run(ctx)
    ctx.results["cost_agent"] = CostAgent().run(ctx)

    opt_agent = OptimizationAgent()
    # build opt_input
    opt_input = OptimizationInput(
        vehicles=ctx.results["battery_agent"].outputs["battery_table"],
        trips=tables["trips"],
        eligibility=ctx.results["route_agent"].outputs["eligibility"],
        chargers=tables["chargers"],
        charger_capacity=ctx.results["charging_agent"].outputs["charger_capacity"],
        site_capacity=ctx.results["charging_agent"].outputs["site_capacity"],
        prices=ctx.results["cost_agent"].outputs["prices"],
        cost_params=ctx.results["cost_agent"].outputs["cost_params"],
        n_slots=config.horizon.n_slots,
        slot_hours=config.horizon.slot_hours,
    )
    return config, opt_input, tables


def test_optimized_plan_passes_validator(base_opt_input):
    config, opt_input, _ = base_opt_input
    plan = solve(opt_input, config)
    violations = validate_plan(plan, opt_input, config)
    assert len(violations) == 0, f"Violations found: {[v.message for v in violations]}"


def test_optimized_cost_le_baseline_cost(base_opt_input):
    config, opt_input, _ = base_opt_input
    baseline = build_baseline_plan(opt_input, config)
    optimized = solve(opt_input, config)

    base_eval = evaluate_plan(baseline, opt_input, config)
    opt_eval = evaluate_plan(optimized, opt_input, config)

    assert opt_eval["operating_cost"] <= base_eval["operating_cost"] + 1e-2


def test_flat_prices_no_phantom_savings(base_opt_input):
    config, opt_input, _ = base_opt_input
    flat_prices = opt_input.prices.copy()
    flat_prices["price_per_kwh"] = 5.0
    flat_opt_input = copy.copy(opt_input)
    flat_opt_input.prices = flat_prices
    
    flat_config = copy.deepcopy(config)
    flat_config.tariff.demand_charge_per_kw = 0.0
    flat_opt_input.cost_params["demand_charge_per_kw"] = 0.0

    baseline = build_baseline_plan(flat_opt_input, flat_config)
    optimized = solve(flat_opt_input, flat_config)

    base_eval = evaluate_plan(baseline, flat_opt_input, flat_config)
    opt_eval = evaluate_plan(optimized, flat_opt_input, flat_config)

    # With flat prices and 0 demand charge, optimized operating cost is <= baseline operating cost
    assert opt_eval["operating_cost"] <= base_eval["operating_cost"] + 1e-2
    assert opt_eval["total_cost"] <= base_eval["total_cost"] + 1e-2


def test_price_spike_scenario_shifts_energy(base_opt_input):
    config, opt_input, tables = base_opt_input
    spike_tables = apply_scenario("price_spike", tables, config)
    spike_prices = spike_tables["tariffs"]
    spike_input = copy.copy(opt_input)
    spike_input.prices = spike_prices

    baseline = build_baseline_plan(spike_input, config)
    optimized = solve(spike_input, config)

    # Calculate energy charged during spike window (slots 16 to 32)
    base_spike_kwh = baseline.charging[baseline.charging["slot"].isin(range(16, 32))]["energy_to_battery_kwh"].sum()
    opt_spike_kwh = optimized.charging[optimized.charging["slot"].isin(range(16, 32))]["energy_to_battery_kwh"].sum()

    assert opt_spike_kwh <= base_spike_kwh


def test_tight_site_limit(base_opt_input):
    config, opt_input, _ = base_opt_input
    tight_config = copy.deepcopy(config)
    tight_config.site.limit_kw = 15.0  # severely tight
    tight_config.solver.time_limit_s = 5
    tight_input = copy.copy(opt_input)
    tight_input.site_capacity = pd.DataFrame([{"slot": s, "site_limit_kw": 15.0} for s in range(opt_input.n_slots)])

    plan = solve(tight_input, tight_config)
    violations = validate_plan(plan, tight_input, tight_config)
    # No physical violations (charge while away, over site limit)
    assert not any(v.type == "site_limit_exceeded" for v in violations)


def test_impossible_trip_unserved(base_opt_input):
    config, opt_input, _ = base_opt_input
    # Mark trip 0 ineligible for all vehicles
    elig = opt_input.eligibility.copy()
    first_trip = opt_input.trips["trip_id"].iloc[0]
    elig.loc[elig["trip_id"] == first_trip, "eligible"] = False

    mod_input = copy.copy(opt_input)
    mod_input.eligibility = elig

    plan = solve(mod_input, config)
    assert plan is not None
    unserved = plan.assignments[plan.assignments["trip_id"] == first_trip]
    assert not unserved.empty
    assert unserved.iloc[0]["served"] == False


def test_charger_count_holds_after_postprocessing(base_opt_input):
    config, opt_input, _ = base_opt_input
    plan = solve(opt_input, config)
    # Check physical charger assignment
    assert "charger_id" in plan.charging.columns
    # Check that in each slot, no physical charger is assigned more than once
    active_chg = plan.charging[plan.charging["power_kw_grid"] > 1e-4]
    for (s, ch_id), group in active_chg.groupby(["slot", "charger_id"]):
        if ch_id != "UNASSIGNED":
            assert len(group) == 1, f"Charger {ch_id} assigned to multiple vehicles at slot {s}"


def test_heuristic_fallback_clean_plan(base_opt_input):
    config, opt_input, _ = base_opt_input
    plan = build_heuristic_plan(opt_input, config)
    violations = validate_plan(plan, opt_input, config)
    assert len(violations) == 0, f"Violations found: {[v.message for v in violations]}"


def test_validator_catches_corrupted_plans(base_opt_input):
    config, opt_input, _ = base_opt_input
    plan = solve(opt_input, config)
    
    # 1. Corrupt plan by setting power above site limit
    corrupted_plan = copy.deepcopy(plan)
    if not corrupted_plan.charging.empty:
        corrupted_plan.charging.loc[0, "power_kw_grid"] = 500.0
        corrupted_plan.charging.loc[0, "slot"] = 0
        v_corrupt = validate_plan(corrupted_plan, opt_input, config)
        assert any(v.type == "site_limit_exceeded" for v in v_corrupt)


def test_determinism(base_opt_input):
    config, opt_input, _ = base_opt_input
    plan1 = solve(opt_input, config)
    plan2 = solve(opt_input, config)

    eval1 = evaluate_plan(plan1, opt_input, config)
    eval2 = evaluate_plan(plan2, opt_input, config)

    assert eval1["operating_cost"] == eval2["operating_cost"]
