"""Pipeline orchestrator for executing the 7-agent workflow and exporting artifacts."""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Optional
import pandas as pd

from src.common.config import Config, load_config
from src.common.schemas import AgentContext, AgentResult, Plan
from src.common.io import export_pipeline_results
from src.data.loaders import load_tables
from src.data.validators import validate_tables
from src.data.simulator import generate_all_tables, save_tables

from src.agents.fleet_agent import FleetAgent
from src.agents.battery_agent import BatteryAgent
from src.agents.route_agent import RouteAgent
from src.agents.charging_agent import ChargingAgent
from src.agents.cost_agent import CostAgent
from src.agents.optimization_agent import OptimizationAgent
from src.agents.recommendation_agent import RecommendationAgent

logger = logging.getLogger("ev_fleet_optimizer.orchestrator")


def run_pipeline(
    config: Optional[Config] = None,
    tables: Optional[dict[str, pd.DataFrame]] = None,
    output_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Runs the full 7-agent EV fleet energy optimization pipeline."""
    if config is None:
        config = load_config()

    # 1. Load or Generate Input Tables
    if tables is None:
        processed_dir = Path("data/processed")
        if (processed_dir / "vehicles.csv").exists():
            try:
                tables = load_tables(processed_dir)
                if len(tables.get("weather", [])) != config.horizon.n_slots or len(tables.get("vehicles", [])) != config.fleet.n_vehicles:
                    tables = generate_all_tables(config)
                    save_tables(tables, processed_dir)
            except Exception as e:
                logger.warning(f"Failed to load from {processed_dir}: {e}. Generating new tables...")
                tables = generate_all_tables(config)
                save_tables(tables, processed_dir)
        else:
            tables = generate_all_tables(config)
            save_tables(tables, processed_dir)

    validate_tables(tables, config, allow_impossible_trips=True)

    # 2. Sequential Multi-Agent Execution Harness
    ctx = AgentContext(config=config, tables=tables, results={})
    agent_instances = [
        FleetAgent(),
        BatteryAgent(),
        RouteAgent(),
        ChargingAgent(),
        CostAgent(),
        OptimizationAgent(),
        RecommendationAgent(),
    ]

    agent_logs: list[dict[str, Any]] = []

    for agent in agent_instances:
        logger.info(f"Running agent: {agent.name}")
        result: AgentResult = agent.run(ctx)
        ctx.results[agent.name] = result

        agent_logs.append({
            "agent": result.agent,
            "status": result.status,
            "duration_s": result.duration_s,
            "warnings": result.warnings,
            "metrics": result.metrics,
            "timestamp": datetime.datetime.now().isoformat(),
        })

        if result.status == "failed":
            if agent.name in ["fleet_agent", "battery_agent", "route_agent", "charging_agent", "cost_agent"]:
                raise RuntimeError(f"Fatal failure in upstream agent '{agent.name}': {result.warnings}")

    # Extract Plan Results
    opt_res = ctx.results.get("optimization_agent")
    opt_outputs = opt_res.outputs if isinstance(opt_res, AgentResult) else (opt_res.get("outputs", {}) if isinstance(opt_res, dict) else {})
    rec_res = ctx.results.get("recommendation_agent")
    rec_outputs = rec_res.outputs if isinstance(rec_res, AgentResult) else (rec_res.get("outputs", {}) if isinstance(rec_res, dict) else {})

    baseline_plan: Plan = opt_outputs.get("baseline_plan")
    optimized_plan: Plan = opt_outputs.get("optimized_plan")
    kpis: dict[str, Any] = opt_outputs.get("kpis", {})
    recommendations: list[dict[str, Any]] = rec_outputs.get("recommendations", [])
    exec_summary: str = rec_outputs.get("executive_summary", "")

    # 3. Export Artifacts
    now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if output_dir is None:
        output_dir = Path("data/outputs") / f"run_{now_str}"
    else:
        output_dir = Path(output_dir)

    # Compile data source report
    data_sources = {}
    for t_name, df in tables.items():
        if "source" in df.columns:
            sources_present = list(df["source"].unique())
            data_sources[t_name] = sources_present[0] if len(sources_present) == 1 else sources_present
        else:
            data_sources[t_name] = "simulated"

    run_meta = {
        "run_id": f"run_{now_str}",
        "timestamp": datetime.datetime.now().isoformat(),
        "random_seed": config.random_seed,
        "config": config.to_dict(),
        "data_sources": data_sources,
        "executive_summary": exec_summary,
    }

    export_pipeline_results(
        output_dir=output_dir,
        baseline_plan=baseline_plan,
        optimized_plan=optimized_plan,
        kpis=kpis,
        recommendations=recommendations,
        agent_logs=agent_logs,
        run_meta=run_meta,
        copy_to_latest=True,
    )

    return {
        "kpis": kpis,
        "plans": {
            "baseline": baseline_plan,
            "optimized": optimized_plan,
        },
        "recommendations": recommendations,
        "executive_summary": exec_summary,
        "agent_log": agent_logs,
        "output_dir": str(output_dir),
    }
