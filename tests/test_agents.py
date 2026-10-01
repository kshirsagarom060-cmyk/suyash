"""Tests for individual agent modules and the orchestrator harness."""

from __future__ import annotations

from pathlib import Path
import pandas as pd
import pytest

from src.common.config import load_config
from src.common.schemas import AgentContext, AgentResult, OptimizationInput
from src.data.simulator import generate_all_tables
from src.data.scenarios import apply_scenario
from src.agents.fleet_agent import FleetAgent
from src.agents.battery_agent import BatteryAgent
from src.agents.route_agent import RouteAgent
from src.agents.charging_agent import ChargingAgent
from src.agents.cost_agent import CostAgent
from src.agents.optimization_agent import OptimizationAgent
from src.agents.recommendation_agent import RecommendationAgent
from src.agents.orchestrator import run_pipeline


@pytest.fixture
def small_scenario():
    """Small fixture with 4 vehicles, 3 trips, 2 chargers, 48 slots."""
    config = load_config(overrides={
        "random_seed": 42,
        "fleet": {"n_vehicles": 4},
        "chargers": {
            "ac": {"count": 1, "power_kw": 11.0, "efficiency": 0.92, "derate_factor": 1.0},
            "dc": {"count": 1, "power_kw": 30.0, "efficiency": 0.94, "derate_factor": 0.85},
        },
        "horizon": {"n_slots": 48, "slot_minutes": 15},
    })
    tables = generate_all_tables(config)
    return config, tables


def test_fleet_agent(small_scenario):
    config, tables = small_scenario
    # Set one vehicle to maintenance
    tables["vehicles"].loc[0, "status"] = "maintenance"
    ctx = AgentContext(config=config, tables=tables)

    agent = FleetAgent()
    res = agent.run(ctx)
    assert res.status in ["ok", "warning"]
    usable = res.outputs["usable_vehicles"]
    assert len(usable) == len(tables["vehicles"]) - 1
    assert tables["vehicles"].loc[0, "vehicle_id"] not in set(usable["vehicle_id"])


def test_battery_agent_explicit_calculation(small_scenario):
    config, tables = small_scenario
    ctx = AgentContext(config=config, tables=tables)
    fleet_res = FleetAgent().run(ctx)
    ctx.results["fleet_agent"] = fleet_res

    res = BatteryAgent().run(ctx)
    assert res.status in ["ok", "warning"]
    b_table = res.outputs["battery_table"]

    # Explicit numeric check for first vehicle
    v0 = b_table.iloc[0]
    expected_usable = float(v0["battery_capacity_kwh"]) * float(v0["soh"])
    assert pytest.approx(float(v0["usable_capacity_kwh"]), 0.01) == expected_usable
    expected_e_min = float(config.battery.reserve_soc_pct) / 100.0 * expected_usable
    assert pytest.approx(float(v0["e_min"]), 0.01) == expected_e_min


def test_route_agent_penalties(small_scenario):
    config, tables = small_scenario
    ctx = AgentContext(config=config, tables=tables)
    ctx.results["fleet_agent"] = FleetAgent().run(ctx)
    ctx.results["battery_agent"] = BatteryAgent().run(ctx)

    res = RouteAgent().run(ctx)
    assert res.status in ["ok", "warning"]
    elig = res.outputs["eligibility"]
    assert not elig.empty
    assert "required_departure_kwh" in elig.columns

    # Test temp factor calculation
    assert RouteAgent.calc_temp_factor(24.0) == 1.0
    assert RouteAgent.calc_temp_factor(10.0) > 1.0  # Cold penalty
    assert RouteAgent.calc_temp_factor(35.0) > 1.0  # Hot penalty


def test_charging_agent_outages_and_compatibility(small_scenario):
    config, tables = small_scenario
    # Apply charger outage scenario
    tables = apply_scenario("charger_outage", tables, config)
    ctx = AgentContext(config=config, tables=tables)
    ctx.results["fleet_agent"] = FleetAgent().run(ctx)
    ctx.results["battery_agent"] = BatteryAgent().run(ctx)
    ctx.results["route_agent"] = RouteAgent().run(ctx)

    res = ChargingAgent().run(ctx)
    assert res.status in ["ok", "warning"]
    cap_df = res.outputs["charger_capacity"]
    # Verify reduced capacity during outage slot 10
    slot10_ac = cap_df[(cap_df["slot"] == 10) & (cap_df["type"] == "AC")]
    assert slot10_ac.iloc[0]["available_count"] == 0  # 1 AC charger out of 1 is disabled


def test_cost_agent(small_scenario):
    config, tables = small_scenario
    ctx = AgentContext(config=config, tables=tables)
    res = CostAgent().run(ctx)
    assert res.status == "ok"
    prices = res.outputs["prices"]
    assert len(prices) == config.horizon.n_slots


def test_recommendation_agent_unserved_rule(small_scenario):
    config, tables = small_scenario
    ctx = AgentContext(config=config, tables=tables)
    ctx.results["fleet_agent"] = FleetAgent().run(ctx)
    ctx.results["battery_agent"] = BatteryAgent().run(ctx)
    ctx.results["route_agent"] = RouteAgent().run(ctx)
    ctx.results["charging_agent"] = ChargingAgent().run(ctx)
    ctx.results["cost_agent"] = CostAgent().run(ctx)
    ctx.results["optimization_agent"] = OptimizationAgent().run(ctx)

    res = RecommendationAgent().run(ctx)
    assert res.status == "ok"
    recs = res.outputs["recommendations"]
    assert isinstance(recs, list)
    assert len(recs) <= 15
    assert len(res.outputs["executive_summary"]) > 0


def test_orchestrator_end_to_end(small_scenario, tmp_path):
    config, tables = small_scenario
    out_dir = tmp_path / "test_run"
    result = run_pipeline(config=config, tables=tables, output_dir=out_dir)

    assert "kpis" in result
    assert "plans" in result
    assert "recommendations" in result
    assert (out_dir / "charging_schedule.csv").exists()
    assert (out_dir / "assignments.csv").exists()
    assert (out_dir / "soc_trajectory.csv").exists()
    assert (out_dir / "site_load.csv").exists()
    assert (out_dir / "kpis.json").exists()
    assert (out_dir / "recommendations.json").exists()
    assert (out_dir / "agent_log.jsonl").exists()
    assert (out_dir / "run_meta.json").exists()
