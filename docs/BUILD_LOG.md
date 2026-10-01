# Build Log

## Phase 0 — Scaffolding & Setup
- Scaffolded project directories: `config/`, `docs/`, `data/`, `src/`, `scripts/`, `tests/`.
- Created `requirements.txt` pinning `pandas`, `numpy`, `pulp>=2.8.0,<3.0.0`, `streamlit`, `plotly`, `pyyaml`, `pytest`, `requests`, `python-dotenv`.
  - *Note on PuLP*: PuLP 4.0 removed legacy APIs like `LpVariable(cat=...)` and `PULP_CBC_CMD`. Pinned to PuLP 2.9.0 for reliable cross-platform execution.
- Created `config/settings.yaml`, `.env.example`.
- Gate verified: `python -c "import pandas, pulp, streamlit, plotly"` passed cleanly.

## Phase 1 — Data Simulation
- Implemented `src/common/time_grid.py`, `schemas.py`, `config.py`, `logging_utils.py`, `io.py`.
- Implemented `src/data/simulator.py`: synthetic generation of all 6 tables (`vehicles`, `chargers`, `trips`, `tariffs`, `site_limits`, `telemetry_history`).
  - Added efficiency-aware trip distance repair loop so generated trips are physically achievable.
- Implemented `src/data/scenarios.py`: mutators for 6 scenarios (`base`, `price_spike`, `charger_outage`, `heavy_demand`, `low_battery_fleet`, `site_constraint`).
- Implemented `src/data/external.py`: optional real-data adapters for Open-Meteo, OSRM routing, ENTSO-E tariffs, and Google Gemini LLM recommendations, with local disk caching in `data/external_cache/` and graceful fallback to synthetic data.
- Implemented `src/data/loaders.py` and `validators.py` for CSV IO and schema integrity.
- Gate verified: `python -m src.data.simulator --seed 42` and `pytest tests/test_data.py` (6/6 tests passed).

## Phase 2 — Agents + Orchestrator
- Implemented 7 modular agents in `src/agents/`:
  - `FleetAgent`: Availability window calculation, telemetry ingestion, maintenance filters.
  - `BatteryAgent`: Effective capacity estimation, initial energy, target and high-SOC thresholds.
  - `RouteAgent`: Trip energy feasibility estimation and vehicle-trip compatibility matrix.
  - `ChargingAgent`: Slot-level capacity envelopes for AC/DC chargers and site power limits.
  - `CostAgent`: Time-of-use pricing curves and objective function cost weights.
  - `OptimizationAgent`: Preparation of normalized inputs, invocation of MILP solver / heuristic fallback.
  - `RecommendationAgent`: Cost savings breakdown, peak-shift insights, actionable operational recommendations, and optional LLM narrative generation.
- Implemented `Orchestrator` in `src/agents/orchestrator.py`: end-to-end execution, context passing, timestamped and `latest/` artifact persistence.
- Gate verified: `pytest tests/test_agents.py` (7/7 tests passed).

## Phase 3 — Optimization Engine & Validator
- Implemented MILP formulation in `src/optimization/model.py` using PuLP:
  - Decision variables: binary trip assignments, binary charger connections, continuous charging power, energy state of charge, high-SOC dwell, unserved trip flags, and peak site kW.
  - Hard physical constraints: trip coverage, single vehicle per trip, no double booking, no charging during trip, charger type capacity, site maximum power, energy continuity, departure minimum energy.
  - Soft objective terms: electricity cost, peak demand charge, battery throughput wear, high-SOC dwell degradation, unserved trip penalties, end-of-horizon shortfall penalties.
- Implemented `src/optimization/baseline.py`: unmanaged greedy charging upon plug-in, baseline cost evaluation.
- Implemented `src/optimization/heuristic.py`: price-aware greedy heuristic fallback for offline or constrained execution.
- Implemented `src/optimization/validator.py`: independent physical invariant checker catching overcapacity, double-bookings, SOC violations, and power limit breaches.
- Implemented `src/optimization/evaluate.py`: single unified cost evaluator computing identical metrics for both baseline and optimized plans.
- Implemented `src/optimization/postprocess.py`: interval-coloring algorithm mapping charger type decisions to concrete physical charger IDs.
- Implemented `scripts/run_pipeline.py` CLI runner.
- Gate verified: `pytest tests/test_optimization.py` (10/10 tests passed) and `python scripts/run_pipeline.py` (85.5% operating cost savings printed).

## Phase 4 — Streamlit Dashboard
- Built interactive Streamlit application in `src/dashboard/`:
  - `src/dashboard/app.py`: Main dashboard entrypoint, KPI summary header, active scenario banner, and page navigation.
  - `src/dashboard/pages/01_overview.py`: Fleet metrics, status breakdown, vehicle telemetry table.
  - `src/dashboard/pages/02_charging_schedule.py`: Gantt schedule per charger and vehicle, power heatmaps.
  - `src/dashboard/pages/03_trip_allocation.py`: Trip timeline, vehicle assignments, departure SOC vs requirement.
  - `src/dashboard/pages/04_cost_analytics.py`: Electricity price curves, power demand vs site limit, baseline vs optimized cost comparison.
  - `src/dashboard/pages/05_recommendations.py`: Operational savings breakdown, peak shaving analysis, smart recommendations, optional Gemini LLM advice.
  - `src/dashboard/pages/06_scenarios.py`: Interactive scenario selector with live one-click re-optimization and KPI diff comparisons.
  - `src/dashboard/charts.py`, `components.py`, `data_access.py`: Reusable Plotly charts and data loading utilities.
- Gate verified: `streamlit run src/dashboard/app.py` launched cleanly, and `pytest tests/test_dashboard.py` (3/3 tests passed).

## Phase 5 — Polish & Verification
- Resolved Windows-specific CBC solver threading issue by enforcing `threads=None` when running on `win32` (CBC 2.10.3 on Windows deadlocks when thread options are passed).
- Confirmed charger connection decision variables `y[v, g, t]` are defined with `cat=pulp.LpBinary` to strictly guarantee physical charger slot capacity.
- Created comprehensive `README.md` and updated `docs/DECISIONS.md`.
- Gate verified: Full test suite `pytest -q` passed with 26/26 tests green (100%).

## Deployment
- Successfully deployed Streamlit application as a live background service on port 8501 (`http://localhost:8501`).
- Verified health check endpoint `/_stcore/health` (HTTP 200 OK).
- Created container deployment assets (`Dockerfile` and `docker-compose.yml`) with automated simulation warmup, healthcheck, and CBC solver support.
