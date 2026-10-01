# AI Energy & EV Fleet Optimization Agent — Project Instructions

You are a senior Python engineer building a working, tested prototype of an
**AI Energy & EV Fleet Optimization Agent**. The system decides which EVs to
charge, when, at which charger, and which vehicle serves which trip, so that
electricity cost and battery wear are minimized while every critical trip is
still served.

This file is the entry point. The detailed specifications live in `prompts/`.
**Read every prompt file completely before writing code**, then build in the
phases below.

## Reading order (mandatory)

1. `prompts/00_shared_contract.md` — folder layout, units, schemas, config, interfaces. Single source of truth.
2. `prompts/01_data_simulation.md` — simulated data generator + optional real-data adapters.
3. `prompts/02_agent_modules.md` — the seven agents and the orchestrator.
4. `prompts/03_optimization.md` — MILP model, baseline policy, plan validator, fallback heuristic.
5. `prompts/04_dashboard.md` — Streamlit dashboard.

If two files disagree, `00_shared_contract.md` wins. If something is genuinely
ambiguous, pick the simplest reasonable option, document it in `docs/DECISIONS.md`, and continue. Do not stop to ask unless blocked.

## Working rules

- Complete, runnable code only. No pseudocode, no `TODO` stubs, no placeholder functions.
- Python 3.10+. Type hints and docstrings on all public functions.
- Pin dependencies in `requirements.txt` (pandas, numpy, pulp, ortools, plotly, streamlit, pyyaml, pytest, requests, python-dotenv).
- No hard-coded numbers in logic. All tunables come from `config/settings.yaml`.
- Never invent results. Every KPI shown anywhere must be computed from the data and the solver output. Savings = baseline cost − optimized cost, both evaluated by the **same** cost function on the **same** data.
- Real-data features are optional and must fail gracefully back to simulated data, with a visible `source` label.
- Secrets only via environment variables or `.env` (never committed). Provide `.env.example`.
- After each phase, run its gate command. Fix failures before moving on.
- Keep a short `docs/BUILD_LOG.md` listing what you built per phase and any deviations.

## Build phases and gates

| Phase | Build | Gate (must pass) |
|---|---|---|
| 0 | Scaffold folders, `requirements.txt`, `config/settings.yaml`, `.env.example`, venv instructions | `python -c "import pandas, pulp, streamlit, plotly"` |
| 1 | Data simulation (prompt 01) | `python -m src.data.simulator --seed 42` then `pytest tests/test_data.py` |
| 2 | Agents + orchestrator, with a simple baseline (prompt 02) | `pytest tests/test_agents.py` |
| 3 | Optimization engine + validator (prompt 03) | `pytest tests/test_optimization.py` and `python scripts/run_pipeline.py` prints baseline vs optimized KPIs |
| 4 | Dashboard (prompt 04) | `streamlit run src/dashboard/app.py` starts without errors; `pytest tests/test_dashboard.py` |
| 5 | Polish: README, docs, full test run | `pytest -q` all green |

## Run instructions (put these in README.md as well)

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m src.data.simulator --seed 42
python scripts/run_pipeline.py
streamlit run src/dashboard/app.py
```

macOS / Linux:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m src.data.simulator --seed 42
python scripts/run_pipeline.py
streamlit run src/dashboard/app.py
```

## Definition of done

- One command generates data, runs all agents, solves, and writes results to `data/outputs/latest/`.
- Dashboard shows fleet overview, charging schedule, trip allocation, cost analytics, recommendations and alerts, and a scenario panel.
- Optimized plan passes the independent plan validator with zero hard-constraint violations.
- Optimized cost is less than or equal to baseline cost on the default scenario, or the dashboard clearly explains why not.
- All tests pass.
