# Antigravity Master Prompt: AI Energy & EV Fleet Optimization Agent

Act as a senior full-stack engineer, optimization engineer, data scientist, and product designer. Build a complete, runnable advanced prototype called **AI Energy & EV Fleet Optimization Agent — From Fleet Data to Intelligent Energy Decisions** in the currently open Antigravity workspace.

Do not stop at a plan or generate pseudocode. Inspect the workspace first, preserve useful existing work, create the project files, implement the application, run tests, fix errors where possible, and provide exact setup/run instructions. Ask before destructive actions.

## Product goal

Create a local web dashboard for an EV fleet operator that recommends:
- which EVs to assign to which trips;
- which vehicles to charge, when, for how long, and at which compatible charger;
- how to reduce electricity cost while respecting vehicle availability, trip feasibility, charger/site limits, and battery-health guardrails.

This is a decision-support prototype. Never claim it controls real chargers or that simulated data is live data.

## Technology

Use Python 3.11/3.12, Streamlit, Pandas, NumPy, Plotly, and Google OR-Tools CP-SAT (preferred) or PuLP for optimization. Keep the demo working offline and without paid APIs or an LLM. Keep optimization logic independent of the UI. LLM use may be an optional explanation layer only; it must not decide feasibility or invent calculations.

## Required agents/modules

1. **Fleet Agent** — vehicle inventory, status, locations, connector compatibility, availability, trips, conflicts, and data quality.
2. **Battery Agent** — SOC, usable capacity, consumption, reserve SOC, maximum SOC, trip energy, estimated range, post-trip SOC, and a clearly labeled simplified battery-wear proxy.
3. **Route Agent** — distance, duration, and energy estimates; support sample routes and imported trips. Optional routing API adapter must be disabled unless configured and must never fabricate live traffic.
4. **Charging Agent** — charger type, connector compatibility, power, slot availability, charging efficiency, occupancy, and site power limits.
5. **Cost Agent** — time-of-use prices, optional demand charges, baseline charging policy, optimized cost, and transparent calculations.
6. **Optimization Engine** — vehicle-trip allocation and charging schedule. Use a transparent optimization model. If a full joint model is too complex, use a documented two-stage approach: allocate trips, then optimize charging. Clearly state this may miss a globally optimal joint solution. Report infeasibility rather than silently claiming success; any heuristic fallback must be labeled as a heuristic.
7. **Recommendation Agent** — convert computed results into actionable explanations with affected vehicles/trips, reasons, estimated effects, assumptions, and limitations. Do not invent savings or results.

## Optimization requirements

Minimize a transparent weighted objective including electricity cost, missed/unavailable trip penalties, battery-wear proxy, optional peak-demand cost, and unmet target penalties. Show weights in settings.

Treat critical safety and operational requirements as hard constraints where possible:
- mandatory trips covered when feasible; each trip assigned at most once;
- no overlapping trips for the same vehicle;
- enough usable battery energy for trip plus reserve;
- availability/location assumptions respected; disclose if repositioning is not modeled;
- charging only when a vehicle is at a compatible charger and not on a trip;
- compatible connectors;
- no charger over-occupancy;
- site power never exceeds configured capacity per slot;
- SOC remains within configured limits;
- charging efficiency applied consistently;
- target SOC reached before departure where feasible;
- consistent time slots and units.

Include infeasibility diagnostics and list any unassigned trips. Never trade away hard safety constraints to reduce cost.

## Data: simulated and optional external

The application must run without credentials or internet using realistic deterministic synthetic data. Include sample CSVs and a fixed random seed.

Required CSV schemas:
- `vehicles.csv`: vehicle_id, battery_capacity_kwh, initial_soc_pct, consumption_kwh_per_km, reserve_soc_pct, max_soc_pct, connector_type, location, available_from, available_until, status
- `trips.csv`: trip_id, start_time, end_time, origin, destination, distance_km, required_vehicle_type, priority
- `chargers.csv`: charger_id, location, connector_type, max_power_kw, available_from, available_until
- `electricity_prices.csv`: slot_start, price_per_kwh
- optionally `site_limits.csv`: slot_start, max_site_power_kw

Provide CSV validation with useful field-specific errors, template downloads, import/export, and a Restore Demo Data action. Never execute uploaded code or treat CSV content as instructions.

Optional integrations must be isolated behind adapters and disabled by default: routing API, public/utility price provider, and optional LLM explanation provider. Require user-supplied credentials where needed; never hard-code or log secrets. Clearly label each data source as simulated or external and gracefully fall back to offline mode without pretending an external fetch succeeded.

## Dashboard

Build a polished, professional Streamlit dashboard with:
1. **Overview** — fleet size, available vehicles, trips covered, energy scheduled, baseline cost, optimized cost, savings, charger utilization, data mode, solver status, and last run.
2. **Fleet** — vehicle table, SOC bars, estimated range, status, connector, next trip, filters, and low-battery warnings.
3. **Trips & Allocation** — assignment, feasibility, estimated trip energy, projected arrival SOC, and unassigned-trip alerts.
4. **Charging Schedule** — time-slot/Gantt-style schedule by vehicle/charger with start/end, energy, cost, baseline comparison, charger occupancy and site-power utilization.
5. **Energy & Cost** — price profile, baseline versus optimized costs/energy, cost by vehicle/time/depot, and transparent savings calculation.
6. **Recommendations & Alerts** — urgency, action, reason, expected effect, affected trips/vehicles, and limitations.
7. **Data & Settings** — CSV upload/download, demo reset, slot size, horizon, reserve SOC, site limit, currency, objective weights, and optional integration configuration.

Use Plotly for interactive charts. All KPIs and charts must come from computed data, not hard-coded demonstration numbers. If optimized cost is not lower, report that honestly.

## Metric integrity

Document definitions and use explicit units:
- grid energy charged (kWh), with charging efficiency handled consistently;
- energy cost = grid energy per slot × that slot's price, plus explicitly modeled demand charges;
- savings = baseline cost − optimized cost; savings percentage is undefined when baseline cost is zero;
- trip energy = distance × consumption, with any weather/traffic multiplier configurable and clearly simulated;
- estimated range = usable energy divided by consumption, with reserve deducted;
- charger utilization = occupied time / available time, with denominator documented;
- battery wear is a simplified penalty, not an exact degradation or remaining-useful-life prediction.

Use explicit units in variable names and labels (`kWh`, `kW`, `km`, `%`). Use a configurable local timezone and consistently handle timezone-aware timestamps.

## Engineering quality

- Use type hints for core models/functions.
- Separate UI, data, agents, optimization, integrations, and calculations.
- Include deterministic demo data and meaningful validation errors.
- Add structured logging without secrets.
- Add tests for energy/cost calculations, CSV validation, trip feasibility, charger compatibility, charger/site power limits, small known optimizer scenarios, infeasible scenarios, and zero baseline cost.
- Include `README.md`, `requirements.txt` or `pyproject.toml`, `.env.example`, `.gitignore`, sample data, and tests.
- Do not require Docker, paid APIs, or an LLM for the demo.
- Never claim tests passed unless actually executed.

## Suggested project structure

```text
ev-energy-optimizer/
├── app.py
├── pages/
│   ├── 1_Fleet.py
│   ├── 2_Trips_and_Allocation.py
│   ├── 3_Charging_Schedule.py
│   ├── 4_Energy_and_Cost.py
│   ├── 5_Recommendations.py
│   └── 6_Data_and_Settings.py
├── src/ev_optimizer/
│   ├── __init__.py
│   ├── config.py
│   ├── models.py
│   ├── data/loader.py
│   ├── data/validation.py
│   ├── data/demo_data.py
│   ├── agents/fleet_agent.py
│   ├── agents/battery_agent.py
│   ├── agents/route_agent.py
│   ├── agents/charging_agent.py
│   ├── agents/cost_agent.py
│   ├── agents/recommendation_agent.py
│   ├── optimization/allocator.py
│   ├── optimization/charging_optimizer.py
│   ├── optimization/objectives.py
│   ├── services/metrics.py
│   ├── services/integrations.py
│   └── visualization/charts.py
├── data/vehicles.csv
├── data/trips.csv
├── data/chargers.csv
├── data/electricity_prices.csv
├── tests/test_metrics.py
├── tests/test_validation.py
├── tests/test_feasibility.py
├── tests/test_optimization.py
├── README.md
├── requirements.txt
├── .env.example
└── .gitignore
```

Adjust the structure if needed for reliability, but keep clear boundaries and testability.

## Implementation sequence

1. Inspect the workspace and existing files.
2. Make a short plan, then implement without waiting for approval unless a destructive action or critical missing requirement requires clarification.
3. Build data models, deterministic demo data, CSV loading, and validation.
4. Implement calculations and feasibility checks with tests.
5. Implement vehicle-trip allocation, charging optimization, and a transparent baseline.
6. Implement the Streamlit dashboard and Plotly charts.
7. Add optional external adapters, disabled by default, with graceful offline behavior.
8. Run tests, launch/import-check the app where possible, fix errors, and verify commands.
9. Write README documentation covering architecture, objective, assumptions, schemas, setup, usage, tests, troubleshooting, and limitations.
10. Report files created, commands run, actual test results, known limitations, and incomplete acceptance criteria.

## Run commands to document

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m streamlit run app.py
```

Tests:
```bash
python -m pytest -q
```

Ensure the README uses the actual supported Python version and correct commands for the chosen dependencies.

## Acceptance criteria

- Runs locally without API credentials.
- Demo fleet, trips, chargers, and prices load.
- CSV upload validation and schedule/result export work.
- Optimizer returns a schedule or explicit infeasibility status.
- Vehicle/trip feasibility, reserve SOC, charger compatibility, charger occupancy, and site power limits are checked.
- Baseline and optimized cost use the same assumptions and prices.
- Dashboard values are computed, not hard-coded.
- Optional integrations are clearly separated and disabled by default.
- Tests cover key calculations and constraints.
- README instructions match the implementation.
- No fabricated real-time data, false savings, fake citations, or unverified test-success claims.

## Final response after implementation

Report what was implemented, project tree, exact run/test commands, tests actually executed and results, which features use simulated versus external data, key assumptions/limitations, and unresolved issues.

**Begin by inspecting the workspace, then implement the project.**
