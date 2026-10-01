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
            {"title": "Peak Shaving", "description": f"Shaved peak power draw by {kpis['savings']['peak_power_shaved_kw']} kW.", "priority": "high"},
            {"title": "ToU Tariff Arbitrage", "description": f"Shifted {kpis['savings']['energy_shifted_kwh']} kWh from peak to off-peak periods.", "priority": "medium"},
            {"title": "Cost Efficiency", "description": f"Generated {savings_pct}% operating savings vs unmanaged baseline.", "priority": "high"},
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
  <title>AI Energy & EV Fleet Optimization Agent | Vercel Deployment</title>
  <meta name="description" content="Commercial EV Fleet Smart Charging and Trip Optimization Dashboard deployed on Vercel.">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Outfit:wght@400;600;700&display=swap" rel="stylesheet">
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    :root {{
      --bg-main: #0B0F19;
      --bg-card: rgba(17, 24, 39, 0.75);
      --bg-card-hover: rgba(31, 41, 55, 0.85);
      --border-color: rgba(255, 255, 255, 0.08);
      --border-accent: rgba(99, 102, 241, 0.3);
      --text-main: #F3F4F6;
      --text-muted: #9CA3AF;
      --accent-indigo: #6366F1;
      --accent-emerald: #10B981;
      --accent-amber: #F59E0B;
      --accent-rose: #EF4444;
      --accent-cyan: #06B6D4;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: radial-gradient(circle at 15% 15%, #151b2e 0%, #0B0F19 100%);
      color: var(--text-main);
      min-height: 100vh;
      line-height: 1.5;
    }}
    header {{
      background: rgba(11, 15, 25, 0.85);
      backdrop-filter: blur(16px);
      border-bottom: 1px solid var(--border-color);
      position: sticky;
      top: 0;
      z-index: 100;
      padding: 1rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }}
    .brand-logo {{
      width: 36px;
      height: 36px;
      border-radius: 8px;
      background: linear-gradient(135deg, var(--accent-indigo), var(--accent-cyan));
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.2rem;
    }}
    .brand-title {{
      font-family: 'Outfit', sans-serif;
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: -0.02em;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      font-size: 0.75rem;
      font-weight: 600;
      padding: 0.25rem 0.65rem;
      border-radius: 9999px;
      background: rgba(16, 185, 129, 0.15);
      color: var(--accent-emerald);
      border: 1px solid rgba(16, 185, 129, 0.3);
    }}
    .badge-dot {{
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--accent-emerald);
      box-shadow: 0 0 8px var(--accent-emerald);
    }}
    main {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 2rem;
    }}
    .scenario-banner {{
      background: var(--bg-card);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 1.25rem 1.5rem;
      margin-bottom: 2rem;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
    }}
    .scenario-controls {{
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}
    .scenario-btn {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 0.45rem 0.9rem;
      border-radius: 8px;
      font-size: 0.85rem;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s ease;
    }}
    .scenario-btn:hover, .scenario-btn.active {{
      background: var(--accent-indigo);
      border-color: var(--accent-indigo);
      box-shadow: 0 4px 14px rgba(99, 102, 241, 0.4);
    }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1.25rem;
      margin-bottom: 2rem;
    }}
    .kpi-card {{
      background: var(--bg-card);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 1.25rem;
      transition: transform 0.2s ease, border-color 0.2s ease;
    }}
    .kpi-card:hover {{
      transform: translateY(-2px);
      border-color: var(--border-accent);
    }}
    .kpi-label {{
      font-size: 0.8rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 0.35rem;
    }}
    .kpi-value {{
      font-family: 'Outfit', sans-serif;
      font-size: 1.75rem;
      font-weight: 700;
      color: #fff;
    }}
    .kpi-sub {{
      font-size: 0.8rem;
      color: var(--accent-emerald);
      margin-top: 0.25rem;
      display: flex;
      align-items: center;
      gap: 0.3rem;
    }}
    .tabs {{
      display: flex;
      gap: 0.5rem;
      border-bottom: 1px solid var(--border-color);
      margin-bottom: 1.5rem;
    }}
    .tab-btn {{
      background: none;
      border: none;
      color: var(--text-muted);
      padding: 0.75rem 1.25rem;
      font-size: 0.95rem;
      font-weight: 500;
      cursor: pointer;
      position: relative;
      transition: color 0.2s ease;
    }}
    .tab-btn:hover {{ color: var(--text-main); }}
    .tab-btn.active {{
      color: var(--accent-indigo);
      font-weight: 600;
    }}
    .tab-btn.active::after {{
      content: '';
      position: absolute;
      bottom: -1px;
      left: 0;
      right: 0;
      height: 2px;
      background: var(--accent-indigo);
      box-shadow: 0 0 10px var(--accent-indigo);
    }}
    .tab-pane {{
      display: none;
    }}
    .tab-pane.active {{
      display: block;
      animation: fadeIn 0.3s ease;
    }}
    @keyframes fadeIn {{
      from {{ opacity: 0; transform: translateY(6px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    .chart-box {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
    }}
    .chart-title {{
      font-family: 'Outfit', sans-serif;
      font-size: 1.1rem;
      font-weight: 600;
      margin-bottom: 1rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.88rem;
    }}
    th, td {{
      padding: 0.8rem 1rem;
      text-align: left;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    }}
    th {{
      color: var(--text-muted);
      font-weight: 600;
      background: rgba(255, 255, 255, 0.02);
    }}
    tr:hover td {{
      background: rgba(255, 255, 255, 0.03);
    }}
    .status-pill {{
      padding: 0.2rem 0.5rem;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 600;
    }}
    .status-available {{ background: rgba(16, 185, 129, 0.15); color: var(--accent-emerald); }}
    .status-charging {{ background: rgba(99, 102, 241, 0.15); color: var(--accent-indigo); }}
    .status-trip {{ background: rgba(245, 158, 11, 0.15); color: var(--accent-amber); }}
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-logo">⚡</div>
      <div>
        <div class="brand-title">AI Energy & EV Fleet Optimization Agent</div>
        <div style="font-size: 0.75rem; color: var(--text-muted);">Commercial Fleet Smart Charging & Trip Scheduler</div>
      </div>
    </div>
    <div style="display: flex; gap: 1rem; align-items: center;">
      <span class="badge"><span class="badge-dot"></span> Vercel Serverless Ready</span>
      <a href="/api/health" target="_blank" style="color: var(--text-muted); text-decoration: none; font-size: 0.8rem;">API Status</a>
    </div>
  </header>

  <main>
    <div class="scenario-banner">
      <div>
        <div style="font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase;">Active Scenario</div>
        <div id="active-scenario-name" style="font-family: 'Outfit', sans-serif; font-size: 1.2rem; font-weight: 700;">Base Scenario (Default)</div>
      </div>
      <div class="scenario-controls">
        <button class="scenario-btn active" onclick="switchScenario('base')">Base</button>
        <button class="scenario-btn" onclick="switchScenario('price_spike')">Price Spike</button>
        <button class="scenario-btn" onclick="switchScenario('charger_outage')">Charger Outage</button>
        <button class="scenario-btn" onclick="switchScenario('heavy_demand')">Heavy Demand</button>
        <button class="scenario-btn" onclick="switchScenario('low_battery_fleet')">Low Battery</button>
        <button class="scenario-btn" onclick="switchScenario('site_constraint')">Site Constraint</button>
      </div>
    </div>

    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">Operating Cost</div>
        <div class="kpi-value" id="kpi-cost">₹0</div>
        <div class="kpi-sub" id="kpi-savings">₹0 saved (0%)</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Energy Electricity Cost</div>
        <div class="kpi-value" id="kpi-elec">₹0</div>
        <div class="kpi-sub" id="kpi-elec-diff">Optimized charging</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Peak Demand Cost</div>
        <div class="kpi-value" id="kpi-demand">₹0</div>
        <div class="kpi-sub" id="kpi-demand-shaved">₹0 demand shaved</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Peak Site Power</div>
        <div class="kpi-value" id="kpi-peak">0 kW</div>
        <div class="kpi-sub" id="kpi-peak-shaved">0 kW shaved</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Energy Shifted Out of Peak</div>
        <div class="kpi-value" id="kpi-shifted">0 kWh</div>
        <div class="kpi-sub">Time-of-Use Arbitrage</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Trips Served</div>
        <div class="kpi-value" id="kpi-trips">0 / 0</div>
        <div class="kpi-sub">100% on critical trips</div>
      </div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" onclick="showTab('tab-fleet')">Fleet Overview</button>
      <button class="tab-btn" onclick="showTab('tab-schedule')">Charging Schedule</button>
      <button class="tab-btn" onclick="showTab('tab-trips')">Trip Allocation</button>
      <button class="tab-btn" onclick="showTab('tab-cost')">Cost & Load Analytics</button>
      <button class="tab-btn" onclick="showTab('tab-recs')">Recommendations</button>
    </div>

    <!-- TAB 1: FLEET -->
    <div id="tab-fleet" class="tab-pane active">
      <div class="chart-box">
        <div class="chart-title">Fleet Vehicles & Telemetry</div>
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
                <th>Depot Status</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB 2: SCHEDULE -->
    <div id="tab-schedule" class="tab-pane">
      <div class="chart-box">
        <div class="chart-title">Charging Load Profile vs Site Limit</div>
        <div id="chart-load" style="height: 380px;"></div>
      </div>
      <div class="chart-box">
        <div class="chart-title">Active Charging Sessions</div>
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
      <div class="chart-box">
        <div class="chart-title">Vehicle-to-Trip Allocations & Departure SoC</div>
        <div style="overflow-x: auto;">
          <table id="trips-table">
            <thead>
              <tr>
                <th>Trip ID</th>
                <th>Vehicle Assigned</th>
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
      <div class="chart-box">
        <div class="chart-title">Baseline vs Optimized Cost Decomposition</div>
        <div id="chart-cost-bar" style="height: 380px;"></div>
      </div>
    </div>

    <!-- TAB 5: RECOMMENDATIONS -->
    <div id="tab-recs" class="tab-pane">
      <div class="chart-box">
        <div class="chart-title">System Insights & Operational Advice</div>
        <div id="recs-list" style="display: flex; flex-direction: column; gap: 1rem;"></div>
      </div>
    </div>
  </main>

  <script>
    let currentData = {initial_json};

    function renderUI(data) {{
      const kpis = data.kpis;
      const opt = kpis.optimized || {{}};
      const base = kpis.baseline || {{}};
      const sav = kpis.savings || {{}};

      // Render KPIs
      document.getElementById('active-scenario-name').innerText = (data.scenario || 'base').replace('_', ' ').toUpperCase() + ' SCENARIO';
      document.getElementById('kpi-cost').innerText = '₹' + (opt.operating_cost ? opt.operating_cost.toLocaleString('en-IN') : '0');
      document.getElementById('kpi-savings').innerText = '₹' + (sav.operating_cost_savings ? sav.operating_cost_savings.toLocaleString('en-IN') : '0') + ' saved (' + (sav.savings_pct || 0) + '%)';
      document.getElementById('kpi-elec').innerText = '₹' + (opt.energy_cost ? opt.energy_cost.toLocaleString('en-IN') : '0');
      document.getElementById('kpi-demand').innerText = '₹' + (opt.demand_cost ? opt.demand_cost.toLocaleString('en-IN') : '0');
      document.getElementById('kpi-peak').innerText = (opt.peak_power_kw || 0) + ' kW';
      document.getElementById('kpi-peak-shaved').innerText = (sav.peak_power_shaved_kw || 0) + ' kW shaved';
      document.getElementById('kpi-shifted').innerText = (sav.energy_shifted_kwh || 0) + ' kWh';
      document.getElementById('kpi-trips').innerText = (opt.trips_served || 0) + ' / ' + (opt.trips_total || 0);

      // Render Fleet Table
      const fleetBody = document.querySelector('#fleet-table tbody');
      fleetBody.innerHTML = '';
      (data.vehicles || []).forEach(v => {{
        const row = document.createElement('tr');
        row.innerHTML = `
          <td><strong>${{v.vehicle_id || ''}}</strong></td>
          <td>${{v.model_type || ''}}</td>
          <td>${{v.battery_capacity_kwh || ''}} kWh</td>
          <td>${{v.current_soc_pct || ''}}%</td>
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
        row.innerHTML = `
          <td>${{c.slot}}</td>
          <td>${{c.timestamp || ''}}</td>
          <td><strong>${{c.vehicle_id || ''}}</strong></td>
          <td>${{c.charger_id || ''}}</td>
          <td><span class="status-pill ${{c.charger_type === 'DC' ? 'status-charging' : 'status-available'}}">${{c.charger_type}}</span></td>
          <td>${{c.power_kw_grid || ''}} kW</td>
          <td>${{c.energy_to_battery_kwh || ''}} kWh</td>
          <td>₹${{c.price_per_kwh || ''}}</td>
        `;
        schedBody.appendChild(row);
      }});

      // Render Trips Table
      const tripBody = document.querySelector('#trips-table tbody');
      tripBody.innerHTML = '';
      (data.assignments || []).forEach(t => {{
        const row = document.createElement('tr');
        row.innerHTML = `
          <td><strong>${{t.trip_id}}</strong></td>
          <td>${{t.vehicle_id || 'UNASSIGNED'}}</td>
          <td><span class="status-pill ${{t.served ? 'status-available' : 'status-trip'}}">${{t.served ? 'SERVED' : 'UNSERVED'}}</span></td>
          <td>${{t.departure_slot || '-'}}</td>
          <td>${{t.return_slot || '-'}}</td>
          <td>${{t.required_energy_kwh || 0}} kWh</td>
          <td>${{t.departure_soc_pct || 0}}%</td>
        `;
        tripBody.appendChild(row);
      }});

      // Render Recommendations
      const recsList = document.getElementById('recs-list');
      recsList.innerHTML = '';
      (data.recommendations || []).forEach(r => {{
        const item = document.createElement('div');
        item.style.padding = '1rem';
        item.style.background = 'rgba(255, 255, 255, 0.03)';
        item.style.borderRadius = '8px';
        item.style.borderLeft = '4px solid var(--accent-indigo)';
        item.innerHTML = `
          <div style="font-weight: 600; font-size: 1rem; color: #fff;">${{r.title || 'Insight'}}</div>
          <div style="color: var(--text-muted); font-size: 0.9rem; margin-top: 0.25rem;">${{r.description || ''}}</div>
        `;
        recsList.appendChild(item);
      }});

      // Render Load Chart
      const siteLoads = data.site_load || [];
      const slots = siteLoads.map(s => s.slot);
      const loads = siteLoads.map(s => s.site_kw || 0);
      const limits = siteLoads.map(s => s.site_limit_kw || 90);
      const prices = siteLoads.map(s => s.price_per_kwh || 5);

      Plotly.newPlot('chart-load', [
        {{ x: slots, y: loads, type: 'scatter', mode: 'lines', name: 'Fleet Power (kW)', line: {{ color: '#6366F1', width: 3 }}, fill: 'tozeroy', fillcolor: 'rgba(99, 102, 241, 0.15)' }},
        {{ x: slots, y: limits, type: 'scatter', mode: 'lines', name: 'Site Limit (kW)', line: {{ color: '#EF4444', dash: 'dash', width: 2 }} }},
        {{ x: slots, y: prices, type: 'scatter', mode: 'lines', name: 'Tariff (₹/kWh)', yaxis: 'y2', line: {{ color: '#F59E0B', width: 2 }} }}
      ], {{
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: {{ color: '#9CA3AF' }},
        margin: {{ t: 20, r: 40, l: 40, b: 40 }},
        xaxis: {{ title: 'Slot (15-min increments)', gridcolor: 'rgba(255, 255, 255, 0.05)' }},
        yaxis: {{ title: 'Power (kW)', gridcolor: 'rgba(255, 255, 255, 0.05)' }},
        yaxis2: {{ title: 'Tariff (₹/kWh)', overlaying: 'y', side: 'right', gridcolor: 'transparent' }},
        legend: {{ orientation: 'h', y: 1.15 }}
      }}, {{ responsive: true }});

      // Render Cost Comparison Bar Chart
      Plotly.newPlot('chart-cost-bar', [
        {{ x: ['Electricity Cost', 'Peak Demand', 'Battery Wear', 'Total Cost'], y: [base.energy_cost || 0, base.demand_cost || 0, base.wear_cost || 0, base.operating_cost || 0], name: 'Baseline', type: 'bar', marker: {{ color: 'rgba(239, 68, 68, 0.75)' }} }},
        {{ x: ['Electricity Cost', 'Peak Demand', 'Battery Wear', 'Total Cost'], y: [opt.energy_cost || 0, opt.demand_cost || 0, opt.wear_cost || 0, opt.operating_cost || 0], name: 'Optimized', type: 'bar', marker: {{ color: 'rgba(16, 185, 129, 0.85)' }} }}
      ], {{
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: {{ color: '#9CA3AF' }},
        barmode: 'group',
        margin: {{ t: 20, r: 20, l: 40, b: 40 }},
        xaxis: {{ gridcolor: 'rgba(255, 255, 255, 0.05)' }},
        yaxis: {{ title: 'Cost (INR ₹)', gridcolor: 'rgba(255, 255, 255, 0.05)' }},
        legend: {{ orientation: 'h', y: 1.15 }}
      }}, {{ responsive: true }});
    }}

    function showTab(tabId) {{
      document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById(tabId).classList.add('active');
      event.target.classList.add('active');
    }}

    async function switchScenario(name) {{
      document.querySelectorAll('.scenario-btn').forEach(btn => btn.classList.remove('active'));
      event.target.classList.add('active');
      document.getElementById('active-scenario-name').innerText = 'Simulating ' + name + '...';

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
