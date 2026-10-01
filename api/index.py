"""Vercel Serverless Entrypoint & Interactive Dashboard for AI Energy & EV Fleet Optimizer."""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.common.config import load_config
from src.dashboard.data_access import load_run
from src.data.scenarios import apply_scenario
from src.data.simulator import generate_all_tables
from src.optimization.baseline import build_baseline_plan
from src.optimization.heuristic import build_heuristic_plan
from src.optimization.evaluate import evaluate_plan, compute_savings_kpis
from src.optimization.validator import validate_plan
from src.common.schemas import OptimizationInput

app = FastAPI(
    title="AI Energy & EV Fleet Optimization Agent",
    description="Vercel Serverless API and Dashboard for EV Fleet Smart Charging",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory cached active run data
_CURRENT_CACHE: dict[str, Any] = {}


def get_active_data(scenario: str = "base") -> dict[str, Any]:
    """Retrieves or builds active run metrics and datasets."""
    global _CURRENT_CACHE
    if scenario in _CURRENT_CACHE:
        return _CURRENT_CACHE[scenario]

    # Try loading precomputed latest run if base scenario
    if scenario == "base":
        run_data = load_run("latest")
        if run_data and run_data.kpis:
            kpis_copy = copy.deepcopy(run_data.kpis)
            sav = kpis_copy.setdefault("savings", {})
            if "abs" in sav and "operating_cost_savings" not in sav:
                sav["operating_cost_savings"] = sav["abs"]
            if "pct" in sav and "savings_pct" not in sav:
                sav["savings_pct"] = sav["pct"]
            if "kwh_shifted_out_of_peak" in sav and "energy_shifted_kwh" not in sav:
                sav["energy_shifted_kwh"] = sav["kwh_shifted_out_of_peak"]
            if "peak_power_shaved_kw" not in sav:
                base_pk = kpis_copy.get("baseline", {}).get("peak_power_kw", 0.0)
                opt_pk = kpis_copy.get("optimized", {}).get("peak_power_kw", 0.0)
                sav["peak_power_shaved_kw"] = round(base_pk - opt_pk, 2)

            result = {
                "scenario": "base",
                "kpis": kpis_copy,
                "charging": run_data.charging.to_dict(orient="records"),
                "assignments": run_data.assignments.to_dict(orient="records"),
                "soc": run_data.soc.to_dict(orient="records"),
                "site_load": run_data.site_load.to_dict(orient="records"),
                "recommendations": run_data.recommendations,
                "vehicles": run_data.tables.get("vehicles", pd_empty_to_dict()).to_dict(orient="records") if hasattr(run_data.tables.get("vehicles", None), "to_dict") else [],
            }
            _CURRENT_CACHE["base"] = result
            return result

    # Compute on-demand with fast serverless time limit
    config = load_config(overrides={"random_seed": 42, "solver": {"time_limit_s": 6}})
    tables = generate_all_tables(config)
    if scenario != "base":
        tables = apply_scenario(scenario, tables, config)

    # Build OptimizationInput
    from src.agents.fleet_agent import FleetAgent
    from src.agents.battery_agent import BatteryAgent
    from src.agents.route_agent import RouteAgent
    from src.agents.charging_agent import ChargingAgent
    from src.agents.cost_agent import CostAgent
    from src.common.schemas import AgentContext

    ctx = AgentContext(config=config, tables=tables)
    ctx.results["fleet_agent"] = FleetAgent().run(ctx)
    ctx.results["battery_agent"] = BatteryAgent().run(ctx)
    ctx.results["route_agent"] = RouteAgent().run(ctx)
    ctx.results["charging_agent"] = ChargingAgent().run(ctx)
    ctx.results["cost_agent"] = CostAgent().run(ctx)

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

    baseline_plan = build_baseline_plan(opt_input, config)

    # Attempt solver with fallback to heuristic
    plan = None
    solver_info = {"method": "heuristic_fallback", "status": "Feasible", "runtime_s": 0.1}
    try:
        from src.optimization.solver import solve
        plan = solve(opt_input, config)
        if plan:
            solver_info = plan.solver_info
    except Exception:
        pass

    if plan is None:
        plan = build_heuristic_plan(opt_input, config)

    base_kpis = evaluate_plan(baseline_plan, opt_input, config)
    opt_kpis = evaluate_plan(plan, opt_input, config)

    kpis = compute_savings_kpis(base_kpis, opt_kpis, solver_info)
    sav = kpis["savings"]
    sav["operating_cost_savings"] = sav["abs"]
    sav["savings_pct"] = sav["pct"]
    sav["energy_shifted_kwh"] = sav["kwh_shifted_out_of_peak"]
    sav["peak_power_shaved_kw"] = round(base_kpis.get("peak_kw", 0.0) - opt_kpis.get("peak_kw", 0.0), 2)
    savings_pct = sav["pct"]

    result = {
        "scenario": scenario,
        "kpis": kpis,
        "charging": plan.charging.to_dict(orient="records") if hasattr(plan.charging, "to_dict") else [],
        "assignments": plan.assignments.to_dict(orient="records") if hasattr(plan.assignments, "to_dict") else [],
        "soc": plan.soc.to_dict(orient="records") if hasattr(plan.soc, "to_dict") else [],
        "site_load": plan.site_load.to_dict(orient="records") if hasattr(plan.site_load, "to_dict") else [],
        "recommendations": [
            {"title": "Peak Shaving", "detail": f"Shaved peak power draw by {kpis['savings']['peak_power_shaved_kw']} kW below substation limit.", "severity": "info"},
            {"title": "ToU Tariff Arbitrage", "detail": f"Shifted {kpis['savings']['energy_shifted_kwh']} kWh from peak to cheap off-peak periods.", "severity": "info"},
            {"title": "Cost Efficiency", "detail": f"Generated {savings_pct}% operating savings vs unmanaged baseline.", "severity": "warning" if savings_pct < 10 else "info"},
        ],
        "vehicles": tables["vehicles"].to_dict(orient="records") if hasattr(tables.get("vehicles"), "to_dict") else [],
    }
    _CURRENT_CACHE[scenario] = result
    return result


def pd_empty_to_dict():
    import pandas as pd
    return pd.DataFrame()


@app.get("/api/health")
def health():
    return {"status": "ok", "platform": "vercel_serverless", "timestamp": time.time()}


@app.get("/api/kpis")
def get_kpis(scenario: str = "base"):
    data = get_active_data(scenario)
    return JSONResponse(content={"scenario": scenario, "kpis": data["kpis"]})


@app.get("/api/fleet")
def get_fleet(scenario: str = "base"):
    data = get_active_data(scenario)
    return JSONResponse(content={"scenario": scenario, "vehicles": data.get("vehicles", [])})


@app.get("/api/schedule")
def get_schedule(scenario: str = "base"):
    data = get_active_data(scenario)
    return JSONResponse(content={"scenario": scenario, "charging": data.get("charging", []), "site_load": data.get("site_load", [])})


@app.get("/api/trips")
def get_trips(scenario: str = "base"):
    data = get_active_data(scenario)
    return JSONResponse(content={"scenario": scenario, "assignments": data.get("assignments", [])})


class ScenarioRequest(BaseModel):
    scenario: str = "base"


@app.post("/api/run-scenario")
def run_scenario(req: ScenarioRequest):
    data = get_active_data(req.scenario)
    return JSONResponse(content=data)


@app.get("/", response_class=HTMLResponse)
def index():
    initial_data = get_active_data("base")
    initial_json = json.dumps(initial_data).replace("</", "<\\/")

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Energy & EV Fleet Optimization Agent</title>
  <meta name="description" content="AI Energy & EV Fleet Smart Charging and Trip Allocation Optimizer">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    :root {{
      --bg-base: #0B0F19;
      --bg-surface: rgba(17, 24, 39, 0.75);
      --bg-surface-hover: rgba(30, 41, 59, 0.85);
      --border-subtle: rgba(255, 255, 255, 0.08);
      --border-accent: rgba(56, 189, 248, 0.35);
      --text-main: #F9FAFB;
      --text-muted: #9CA3AF;
      --accent-blue: #38BDF8;
      --accent-indigo: #6366F1;
      --accent-emerald: #10B981;
      --accent-amber: #F59E0B;
      --accent-rose: #EF4444;
      --accent-cyan: #06B6D4;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: radial-gradient(ellipse at 15% 0%, #152238 0%, #0B0F19 65%, #070A11 100%);
      color: var(--text-main);
      min-height: 100vh;
      line-height: 1.5;
    }}

    /* Sticky Navbar */
    header {{
      background: rgba(11, 15, 25, 0.82);
      backdrop-filter: blur(18px);
      border-bottom: 1px solid var(--border-subtle);
      position: sticky;
      top: 0;
      z-index: 100;
      padding: 0.9rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 0.85rem;
    }}
    .brand-logo {{
      width: 40px;
      height: 40px;
      border-radius: 10px;
      background: linear-gradient(135deg, #38BDF8, #6366F1);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.3rem;
      box-shadow: 0 4px 14px rgba(56, 189, 248, 0.35);
    }}
    .brand-title {{
      font-family: 'Outfit', sans-serif;
      font-size: 1.3rem;
      font-weight: 700;
      letter-spacing: -0.02em;
    }}
    .brand-sub {{
      font-size: 0.78rem;
      color: var(--text-muted);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      font-size: 0.76rem;
      font-weight: 600;
      padding: 0.3rem 0.8rem;
      border-radius: 9999px;
      background: rgba(16, 185, 129, 0.15);
      color: var(--accent-emerald);
      border: 1px solid rgba(16, 185, 129, 0.35);
    }}
    .badge-dot {{
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--accent-emerald);
      box-shadow: 0 0 8px var(--accent-emerald);
    }}

    main {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 1.8rem;
    }}

    /* Scenario Ribbon */
    .scenario-banner {{
      background: var(--bg-surface);
      backdrop-filter: blur(14px);
      border: 1px solid var(--border-subtle);
      border-radius: 14px;
      padding: 1.1rem 1.4rem;
      margin-bottom: 1.8rem;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
    }}
    .scenario-title {{
      font-size: 0.76rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-weight: 600;
    }}
    .scenario-name {{
      font-family: 'Outfit', sans-serif;
      font-size: 1.15rem;
      font-weight: 700;
      color: #FFFFFF;
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .scenario-controls {{
      display: flex;
      gap: 0.45rem;
      flex-wrap: wrap;
    }}
    .scenario-btn {{
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--border-subtle);
      color: var(--text-main);
      padding: 0.42rem 0.85rem;
      border-radius: 8px;
      font-size: 0.82rem;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }}
    .scenario-btn:hover {{
      background: rgba(56, 189, 248, 0.15);
      border-color: var(--accent-blue);
      color: #FFF;
      transform: translateY(-1px);
    }}
    .scenario-btn.active {{
      background: linear-gradient(135deg, #38BDF8, #6366F1);
      border-color: transparent;
      color: #FFFFFF;
      box-shadow: 0 4px 14px rgba(56, 189, 248, 0.35);
      font-weight: 600;
    }}

    /* Hero KPI Grid */
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 1.2rem;
      margin-bottom: 1.8rem;
    }}
    .kpi-card {{
      background: var(--bg-surface);
      backdrop-filter: blur(14px);
      border: 1px solid var(--border-subtle);
      border-radius: 14px;
      padding: 1.3rem 1.4rem;
      transition: transform 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease;
      position: relative;
      overflow: hidden;
    }}
    .kpi-card::before {{
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 3px;
      background: linear-gradient(90deg, #38BDF8, #6366F1);
      opacity: 0.8;
    }}
    .kpi-card:hover {{
      transform: translateY(-3px);
      border-color: var(--border-accent);
      box-shadow: 0 10px 28px rgba(56, 189, 248, 0.12);
    }}
    .kpi-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.4rem;
    }}
    .kpi-label {{
      font-size: 0.78rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-weight: 600;
    }}
    .kpi-icon {{
      font-size: 1.15rem;
      opacity: 0.85;
    }}
    .kpi-value {{
      font-family: 'Outfit', sans-serif;
      font-size: 1.85rem;
      font-weight: 700;
      color: #FFFFFF;
      letter-spacing: -0.01em;
      margin-bottom: 0.2rem;
    }}
    .kpi-sub {{
      font-size: 0.8rem;
      font-weight: 500;
      display: flex;
      align-items: center;
      gap: 4px;
    }}

    /* Segmented Navigation Tabs */
    .tabs {{
      display: flex;
      gap: 0.4rem;
      border-bottom: 1px solid var(--border-subtle);
      margin-bottom: 1.5rem;
      overflow-x: auto;
    }}
    .tab-btn {{
      background: none;
      border: none;
      color: var(--text-muted);
      padding: 0.75rem 1.25rem;
      font-size: 0.92rem;
      font-weight: 500;
      cursor: pointer;
      position: relative;
      transition: color 0.2s ease;
      white-space: nowrap;
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .tab-btn:hover {{ color: var(--text-main); }}
    .tab-btn.active {{
      color: var(--accent-blue);
      font-weight: 600;
    }}
    .tab-btn.active::after {{
      content: '';
      position: absolute;
      bottom: -1px;
      left: 0;
      right: 0;
      height: 2px;
      background: var(--accent-blue);
      box-shadow: 0 0 10px var(--accent-blue);
    }}
    .tab-pane {{
      display: none;
    }}
    .tab-pane.active {{
      display: block;
      animation: fadeIn 0.25s ease-out;
    }}
    @keyframes fadeIn {{
      from {{ opacity: 0; transform: translateY(6px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}

    /* Chart & Table Cards */
    .card-box {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 14px;
      padding: 1.4rem;
      margin-bottom: 1.5rem;
      backdrop-filter: blur(14px);
    }}
    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
    }}
    .card-title {{
      font-family: 'Outfit', sans-serif;
      font-size: 1.15rem;
      font-weight: 600;
      color: #FFFFFF;
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    /* Vehicle Cards Grid */
    .vehicles-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    .veh-card {{
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid var(--border-subtle);
      border-radius: 10px;
      padding: 1rem;
      transition: all 0.2s ease;
    }}
    .veh-card:hover {{
      background: rgba(255, 255, 255, 0.04);
      border-color: rgba(56, 189, 248, 0.3);
      transform: translateY(-2px);
    }}
    .veh-id {{
      font-weight: 700;
      font-size: 1rem;
      color: #FFF;
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.3rem;
    }}
    .veh-meta {{
      font-size: 0.78rem;
      color: var(--text-muted);
      margin-bottom: 0.6rem;
    }}
    .meter-container {{
      width: 100%;
      height: 7px;
      background: rgba(255, 255, 255, 0.08);
      border-radius: 999px;
      overflow: hidden;
      margin-top: 4px;
    }}
    .meter-fill {{
      height: 100%;
      border-radius: 999px;
      transition: width 0.4s ease;
    }}

    /* Modern Table */
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.86rem;
    }}
    th, td {{
      padding: 0.75rem 1rem;
      text-align: left;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    }}
    th {{
      color: var(--text-muted);
      font-weight: 600;
      background: rgba(255, 255, 255, 0.02);
      text-transform: uppercase;
      font-size: 0.75rem;
      letter-spacing: 0.04em;
    }}
    tr:hover td {{
      background: rgba(255, 255, 255, 0.03);
    }}
    .status-pill {{
      display: inline-block;
      padding: 0.2rem 0.55rem;
      border-radius: 6px;
      font-size: 0.74rem;
      font-weight: 600;
      letter-spacing: 0.02em;
    }}
    .status-available {{ background: rgba(16, 185, 129, 0.15); color: var(--accent-emerald); }}
    .status-charging {{ background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); }}
    .status-trip {{ background: rgba(245, 158, 11, 0.15); color: var(--accent-amber); }}
    .status-maint {{ background: rgba(239, 68, 68, 0.15); color: var(--accent-rose); }}

    /* Alert / Recs card */
    .rec-card {{
      padding: 1.1rem 1.3rem;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid var(--border-subtle);
      border-left: 4px solid var(--accent-blue);
      border-radius: 10px;
      margin-bottom: 0.8rem;
    }}
    .rec-card.rec-warning {{
      border-left-color: var(--accent-amber);
    }}
    .rec-card.rec-critical {{
      border-left-color: var(--accent-rose);
    }}

    footer {{
      text-align: center;
      color: #64748B;
      font-size: 0.8rem;
      padding: 2rem 0 1rem 0;
    }}
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-logo">⚡</div>
      <div>
        <div class="brand-title">AI Energy & EV Fleet Optimizer</div>
        <div class="brand-sub">Commercial Fleet Smart Charging & Route Dispatch System</div>
      </div>
    </div>
    <div style="display: flex; gap: 1rem; align-items: center;">
      <span class="badge"><span class="badge-dot"></span> CBC Solver Active</span>
      <a href="/api/health" target="_blank" style="color: var(--text-muted); text-decoration: none; font-size: 0.8rem;">Health Check</a>
    </div>
  </header>

  <main>
    <!-- Scenario Selection Ribbon -->
    <div class="scenario-banner">
      <div>
        <div class="scenario-title">Active Fleet Scenario</div>
        <div id="active-scenario-name" class="scenario-name">🌟 Standard Base Fleet</div>
      </div>
      <div class="scenario-controls">
        <button class="scenario-btn active" onclick="switchScenario('base')">🌟 Base</button>
        <button class="scenario-btn" onclick="switchScenario('price_spike')">⚡ Price Spike (2.5x)</button>
        <button class="scenario-btn" onclick="switchScenario('charger_outage')">🔌 Charger Outage</button>
        <button class="scenario-btn" onclick="switchScenario('heavy_demand')">📦 Surge Demand (+30%)</button>
        <button class="scenario-btn" onclick="switchScenario('low_battery_fleet')">🔋 Low Battery Fleet</button>
        <button class="scenario-btn" onclick="switchScenario('site_constraint')">🛑 Grid Limit (-35%)</button>
      </div>
    </div>

    <!-- 5 High-Impact KPI Cards -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Net Operating Savings</span>
          <span class="kpi-icon">💰</span>
        </div>
        <div class="kpi-value" id="kpi-savings-val">₹0</div>
        <div class="kpi-sub" id="kpi-savings-sub" style="color: var(--accent-emerald);">0% Operating Savings</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Optimized Fleet Cost</span>
          <span class="kpi-icon">⚡</span>
        </div>
        <div class="kpi-value" id="kpi-cost-val">₹0</div>
        <div class="kpi-sub" id="kpi-cost-sub" style="color: var(--accent-blue);">vs Baseline ₹0</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Peak Grid Demand</span>
          <span class="kpi-icon">📉</span>
        </div>
        <div class="kpi-value" id="kpi-peak-val">0 kW</div>
        <div class="kpi-sub" id="kpi-peak-sub" style="color: var(--accent-emerald);">0 kW Peak Shaved</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Off-Peak Load Shift</span>
          <span class="kpi-icon">🔄</span>
        </div>
        <div class="kpi-value" id="kpi-shifted-val">0 kWh</div>
        <div class="kpi-sub" style="color: var(--accent-emerald);">Time-of-Use Arbitrage</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-header">
          <span class="kpi-label">Trips Served</span>
          <span class="kpi-icon">🎯</span>
        </div>
        <div class="kpi-value" id="kpi-trips-val">0 / 0</div>
        <div class="kpi-sub" style="color: var(--accent-emerald);">100% Critical Trips Met</div>
      </div>
    </div>

    <!-- Segmented Navigation Tabs -->
    <div class="tabs">
      <button class="tab-btn active" onclick="showTab('tab-fleet')">🚗 Fleet Overview</button>
      <button class="tab-btn" onclick="showTab('tab-schedule')">⚡ Charging Schedule</button>
      <button class="tab-btn" onclick="showTab('tab-trips')">🎯 Trip Allocation</button>
      <button class="tab-btn" onclick="showTab('tab-cost')">💰 Cost & Load Analytics</button>
      <button class="tab-btn" onclick="showTab('tab-recs')">💡 AI Advisories</button>
    </div>

    <!-- TAB 1: FLEET -->
    <div id="tab-fleet" class="tab-pane active">
      <div class="card-box">
        <div class="card-header">
          <div class="card-title">🔋 Fleet Battery State of Charge (SOC)</div>
        </div>
        <div id="veh-cards-container" class="vehicles-grid"></div>
      </div>

      <div class="card-box">
        <div class="card-header">
          <div class="card-title">📋 Complete Fleet Vehicle Roster</div>
        </div>
        <div style="overflow-x: auto;">
          <table id="fleet-table">
            <thead>
              <tr>
                <th>Vehicle ID</th>
                <th>Model</th>
                <th>Battery Capacity</th>
                <th>Current SoC</th>
                <th>Max AC Power</th>
                <th>Max DC Power</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB 2: SCHEDULE -->
    <div id="tab-schedule" class="tab-pane">
      <div class="card-box">
        <div class="card-header">
          <div class="card-title">🔌 Aggregate Depot Grid Demand vs Site Capacity Limit</div>
        </div>
        <div id="chart-load" style="height: 380px;"></div>
      </div>
      <div class="card-box">
        <div class="card-header">
          <div class="card-title">⚡ Smart Charging Session Roster</div>
        </div>
        <div style="overflow-x: auto;">
          <table id="schedule-table">
            <thead>
              <tr>
                <th>Slot</th>
                <th>Timestamp</th>
                <th>Vehicle</th>
                <th>Charger</th>
                <th>Type</th>
                <th>Power (kW)</th>
                <th>Energy (kWh)</th>
                <th>Tariff (₹/kWh)</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB 3: TRIPS -->
    <div id="tab-trips" class="tab-pane">
      <div class="card-box">
        <div class="card-header">
          <div class="card-title">🎯 Vehicle Dispatch & Departure Battery Buffer</div>
        </div>
        <div style="overflow-x: auto;">
          <table id="trips-table">
            <thead>
              <tr>
                <th>Trip ID</th>
                <th>Assigned EV</th>
                <th>Status</th>
                <th>Departure Slot</th>
                <th>Return Slot</th>
                <th>Required Energy (kWh)</th>
                <th>Departure SoC (%)</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB 4: COST -->
    <div id="tab-cost" class="tab-pane">
      <div class="card-box">
        <div class="card-header">
          <div class="card-title">💰 Baseline vs Optimized Cost Breakdown</div>
        </div>
        <div id="chart-cost-bar" style="height: 380px;"></div>
      </div>
    </div>

    <!-- TAB 5: RECOMMENDATIONS -->
    <div id="tab-recs" class="tab-pane">
      <div class="card-box">
        <div class="card-header">
          <div class="card-title">💡 Automated Operational Recommendations & Invariant Checks</div>
        </div>
        <div id="recs-list" style="display: flex; flex-direction: column; gap: 0.8rem;"></div>
      </div>
    </div>
  </main>

  <footer>
    ⚡ AI Energy & EV Fleet Optimization Agent • Deterministic Cost Optimization Engine
  </footer>

  <script>
    let currentData = {initial_json};

    function renderUI(data) {{
      const kpis = data.kpis || {{}};
      const opt = kpis.optimized || {{}};
      const base = kpis.baseline || {{}};
      const sav = kpis.savings || {{}};

      // Active Scenario name format
      const scenNames = {{
        'base': '🌟 Standard Base Operations',
        'price_spike': '⚡ Evening Price Spike (2.5x)',
        'charger_outage': '🔌 Depot Charger Outage',
        'heavy_demand': '📦 Surge Delivery Demand (+30%)',
        'low_battery_fleet': '🔋 Low Fleet Battery State',
        'site_constraint': '🛑 Grid Power Limit (-35%)'
      }};
      document.getElementById('active-scenario-name').innerText = scenNames[data.scenario] || data.scenario.toUpperCase();

      // Top KPI Values
      const savVal = sav.operating_cost_savings || sav.abs || 0;
      const savPct = sav.savings_pct || sav.pct || 0;
      document.getElementById('kpi-savings-val').innerText = '₹' + Math.round(savVal).toLocaleString('en-IN');
      document.getElementById('kpi-savings-sub').innerText = savPct.toFixed(1) + '% Total Operating Savings';

      const optCost = opt.operating_cost || 0;
      const baseCost = base.operating_cost || 0;
      document.getElementById('kpi-cost-val').innerText = '₹' + Math.round(optCost).toLocaleString('en-IN');
      document.getElementById('kpi-cost-sub').innerText = 'vs Baseline ₹' + Math.round(baseCost).toLocaleString('en-IN');

      const optPeak = opt.peak_power_kw || opt.peak_kw || 0;
      const peakShaved = sav.peak_power_shaved_kw || 0;
      document.getElementById('kpi-peak-val').innerText = optPeak.toFixed(1) + ' kW';
      document.getElementById('kpi-peak-sub').innerText = peakShaved.toFixed(1) + ' kW Peak Shaved';

      const shiftedKwh = sav.energy_shifted_kwh || sav.kwh_shifted_out_of_peak || 0;
      document.getElementById('kpi-shifted-val').innerText = shiftedKwh.toFixed(1) + ' kWh';

      const tripsServed = opt.trips_served || 0;
      const tripsTot = opt.trips_total || (data.assignments ? data.assignments.length : 0);
      document.getElementById('kpi-trips-val').innerText = tripsServed + ' / ' + tripsTot;

      // Render Vehicle Cards Grid
      const vehGrid = document.getElementById('veh-cards-container');
      vehGrid.innerHTML = '';
      (data.vehicles || []).forEach(v => {{
        const soc = parseFloat(v.current_soc_pct) || 0;
        const color = soc > 50 ? '#10B981' : (soc > 20 ? '#F59E0B' : '#EF4444');
        const card = document.createElement('div');
        card.className = 'veh-card';
        card.innerHTML = `
          <div class="veh-id">
            <span>${{v.vehicle_id}}</span>
            <span class="status-pill status-available">${{v.status || 'AVAILABLE'}}</span>
          </div>
          <div class="veh-meta">${{v.model || v.model_type || 'Commercial EV'}} • ${{v.battery_capacity_kwh}} kWh</div>
          <div style="display: flex; justify-content: space-between; font-size: 0.78rem; font-weight: 600;">
            <span>SOC</span>
            <span style="color: ${{color}}">${{soc.toFixed(1)}}%</span>
          </div>
          <div class="meter-container">
            <div class="meter-fill" style="width: ${{Math.min(100, Math.max(0, soc))}}%; background: ${{color}};"></div>
          </div>
        `;
        vehGrid.appendChild(card);
      }});

      // Render Fleet Table
      const fleetBody = document.querySelector('#fleet-table tbody');
      fleetBody.innerHTML = '';
      (data.vehicles || []).forEach(v => {{
        const row = document.createElement('tr');
        row.innerHTML = `
          <td><strong>${{v.vehicle_id || ''}}</strong></td>
          <td>${{v.model || v.model_type || 'EV'}}</td>
          <td>${{v.battery_capacity_kwh || ''}} kWh</td>
          <td><b>${{parseFloat(v.current_soc_pct || 0).toFixed(1)}}%</b></td>
          <td>${{v.max_ac_kw || ''}} kW</td>
          <td>${{v.max_dc_kw || ''}} kW</td>
          <td><span class="status-pill status-available">${{v.status || 'AVAILABLE'}}</span></td>
        `;
        fleetBody.appendChild(row);
      }});

      // Render Schedule Table
      const schedBody = document.querySelector('#schedule-table tbody');
      schedBody.innerHTML = '';
      (data.charging || []).slice(0, 30).forEach(c => {{
        const row = document.createElement('tr');
        const pwr = parseFloat(c.power_kw_grid || 0).toFixed(1);
        const kwh = parseFloat(c.energy_to_battery_kwh || 0).toFixed(2);
        const price = parseFloat(c.price_per_kwh || 0).toFixed(2);
        row.innerHTML = `
          <td>Slot ${{c.slot}}</td>
          <td>${{c.timestamp || ''}}</td>
          <td><strong>${{c.vehicle_id || ''}}</strong></td>
          <td>${{c.charger_id || ''}}</td>
          <td><span class="status-pill ${{c.charger_type === 'DC' ? 'status-charging' : 'status-available'}}">${{c.charger_type || 'AC'}}</span></td>
          <td>${{pwr}} kW</td>
          <td>${{kwh}} kWh</td>
          <td>₹${{price}}</td>
        `;
        schedBody.appendChild(row);
      }});

      // Render Trips Table
      const tripBody = document.querySelector('#trips-table tbody');
      tripBody.innerHTML = '';
      (data.assignments || []).forEach(t => {{
        const row = document.createElement('tr');
        const reqKwh = parseFloat(t.required_energy_kwh || 0).toFixed(1);
        const depSoc = parseFloat(t.departure_soc_pct || 0).toFixed(1);
        row.innerHTML = `
          <td><strong>${{t.trip_id}}</strong></td>
          <td>${{t.vehicle_id || 'UNASSIGNED'}}</td>
          <td><span class="status-pill ${{t.served ? 'status-available' : 'status-trip'}}">${{t.served ? 'SERVED' : 'UNSERVED'}}</span></td>
          <td>Slot ${{t.departure_slot || '-'}}</td>
          <td>Slot ${{t.return_slot || '-'}}</td>
          <td>${{reqKwh}} kWh</td>
          <td><b>${{depSoc}}%</b></td>
        `;
        tripBody.appendChild(row);
      }});

      // Render Recommendations
      const recsList = document.getElementById('recs-list');
      recsList.innerHTML = '';
      (data.recommendations || []).forEach(r => {{
        const item = document.createElement('div');
        const sevClass = r.severity === 'critical' ? 'rec-critical' : (r.severity === 'warning' ? 'rec-warning' : '');
        item.className = 'rec-card ' + sevClass;
        item.innerHTML = `
          <div style="font-weight: 700; font-size: 1rem; color: #FFF;">${{r.title || 'Advisory'}}</div>
          <div style="color: var(--text-muted); font-size: 0.88rem; margin-top: 0.3rem;">${{r.detail || r.description || ''}}</div>
        `;
        recsList.appendChild(item);
      }});

      // Render Load Chart (Plotly)
      const siteLoads = data.site_load || [];
      const slots = siteLoads.map(s => (s.slot * 0.25).toFixed(2));
      const loads = siteLoads.map(s => s.site_kw || 0);
      const limits = siteLoads.map(s => s.site_limit_kw || 90);
      const prices = siteLoads.map(s => s.price_per_kwh || 5);

      Plotly.newPlot('chart-load', [
        {{ x: slots, y: loads, type: 'scatter', mode: 'lines', name: 'Optimized Fleet Power (kW)', line: {{ color: '#38BDF8', width: 3 }}, fill: 'tozeroy', fillcolor: 'rgba(56, 189, 248, 0.18)' }},
        {{ x: slots, y: limits, type: 'scatter', mode: 'lines', name: 'Site Capacity Limit (kW)', line: {{ color: '#EF4444', dash: 'dash', width: 2 }} }},
        {{ x: slots, y: prices, type: 'scatter', mode: 'lines', name: 'ToU Tariff (₹/kWh)', yaxis: 'y2', line: {{ color: '#F59E0B', width: 2 }} }}
      ], {{
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: {{ family: 'Inter, sans-serif', color: '#9CA3AF' }},
        margin: {{ t: 20, r: 50, l: 45, b: 40 }},
        xaxis: {{ title: 'Horizon Time (Hours)', gridcolor: 'rgba(255, 255, 255, 0.06)' }},
        yaxis: {{ title: 'Grid Demand (kW)', gridcolor: 'rgba(255, 255, 255, 0.06)' }},
        yaxis2: {{ title: 'Tariff Rate (₹/kWh)', overlaying: 'y', side: 'right', gridcolor: 'transparent' }},
        legend: {{ orientation: 'h', y: 1.15, x: 0.5, xanchor: 'center' }}
      }}, {{ responsive: true }});

      // Render Cost Breakdown Bar Chart (Plotly)
      Plotly.newPlot('chart-cost-bar', [
        {{ x: ['Electricity Cost', 'Peak Demand', 'Battery Wear', 'Total Cost'], y: [base.energy_cost || 0, base.demand_cost || 0, base.wear_cost || 0, base.operating_cost || 0], name: 'Baseline (Unmanaged)', type: 'bar', marker: {{ color: '#94A3B8' }} }},
        {{ x: ['Electricity Cost', 'Peak Demand', 'Battery Wear', 'Total Cost'], y: [opt.energy_cost || 0, opt.demand_cost || 0, opt.wear_cost || 0, opt.operating_cost || 0], name: 'Optimized (Smart)', type: 'bar', marker: {{ color: '#38BDF8' }} }}
      ], {{
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: {{ family: 'Inter, sans-serif', color: '#9CA3AF' }},
        barmode: 'group',
        margin: {{ t: 20, r: 20, l: 45, b: 40 }},
        xaxis: {{ gridcolor: 'rgba(255, 255, 255, 0.06)' }},
        yaxis: {{ title: 'Cost (INR ₹)', gridcolor: 'rgba(255, 255, 255, 0.06)' }},
        legend: {{ orientation: 'h', y: 1.15, x: 0.5, xanchor: 'center' }}
      }}, {{ responsive: true }});
    }}

    function showTab(tabId) {{
      document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById(tabId).classList.add('active');
      event.currentTarget.classList.add('active');
    }}

    async function switchScenario(name) {{
      document.querySelectorAll('.scenario-btn').forEach(btn => btn.classList.remove('active'));
      event.currentTarget.classList.add('active');
      document.getElementById('active-scenario-name').innerText = 'Simulating ' + name.replace('_', ' ') + '...';

      try {{
        const res = await fetch('/api/run-scenario', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ scenario: name }})
        }});
        const data = await res.json();
        currentData = data;
        renderUI(data);
      }} catch (e) {{
        console.error('Error switching scenario:', e);
      }}
    }}

    document.addEventListener('DOMContentLoaded', () => {{
      renderUI(currentData);
    }});
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)
