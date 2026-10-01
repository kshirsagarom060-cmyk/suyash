# 04 — Streamlit Dashboard

Read `00_shared_contract.md` first. Implement under `src/dashboard/`.

## Goal

An interactive, honest dashboard that lets a user run the pipeline, inspect the fleet,
compare the baseline and optimized plans, and act on recommendations. It only displays
results computed by the pipeline; it never invents numbers.

Run with: `streamlit run src/dashboard/app.py`

## Structure

```
src/dashboard/
├── app.py            # entry point: page config, sidebar, navigation
├── data_access.py    # loads outputs from data/outputs/latest, cached
├── charts.py         # Plotly figure builders (pure functions: data -> figure)
├── components.py     # KPI cards, alert boxes, tables, download buttons
└── pages/
    ├── 01_overview.py
    ├── 02_charging_schedule.py
    ├── 03_trip_allocation.py
    ├── 04_cost_analytics.py
    ├── 05_recommendations.py
    └── 06_scenarios.py
```

Use Streamlit multipage (`pages/` folder) or `st.navigation`. Wide layout, consistent title, and a footer showing data source labels and run timestamp.

## Global behavior

- **Sidebar**: run selector (latest by default, or any `run_*` folder), a "Run optimization" button, scenario picker, and a small status block (last run time, solver status, data sources used: simulated vs real).
- **Run button** calls `run_pipeline(config)` inside `st.spinner`, then clears caches and reloads. Catch exceptions and show a readable error plus the log tail. Never leave the UI blank.
- **Empty state**: if no outputs exist, show a friendly message and a button that generates simulated data and runs the pipeline.
- Cache loading with `st.cache_data` keyed by run folder and file modification time.
- Plan toggle (Baseline / Optimized / Compare) available on pages that show both plans, stored in `st.session_state`.
- Currency symbol and units come from the config. All tables have download buttons (CSV).
- Colors: consistent palette; peak = red-orange, shoulder = amber, offpeak = green; baseline = grey, optimized = blue. Provide sensible text contrast and hover labels, and do not use color as the only signal.
- Keep each page responsive (fast, under 2 seconds after caching).

## Page 1 — Fleet Overview

- KPI cards: total vehicles, available (usable) vehicles, in maintenance, vehicles charging now (at a time chosen by a slider over the horizon), low-battery alerts, trips scheduled, trips by priority.
- Chart: vehicle SOC at plan start as a sorted bar chart with reserve and ceiling lines.
- Chart: trip demand by hour (stacked by priority) against vehicle supply.
- Table: fleet roster with model, capacity, state of health, current SOC, availability slot, status, with conditional highlighting for low SOC.
- Data-source badge showing which tables are simulated or real.

## Page 2 — Smart Charging Schedule

- **Gantt chart** (Plotly timeline or heatmap): rows = vehicles, x = time, bar color = charger type or power; overlay trip periods in a distinct pattern or color.
- **Price band** under the Gantt: tariff price per slot as a step line with colored periods.
- **Site load chart**: stacked area of site kW by vehicle group, with the site limit as a dashed line; plan toggle; baseline vs optimized comparison mode shows two lines.
- **Vehicle drill-down**: select a vehicle to see its SOC trajectory vs reserve, ceiling, required departure energy, and its charging power by slot.
- Table of charging sessions (vehicle, charger, start, end, kWh, average price).

## Page 3 — Fleet Allocation

- Table: trip, departure/return, distance, priority, assigned vehicle (baseline vs optimized), required energy, departure SOC, margin kWh. Highlight unserved trips and thin margins.
- Chart: trip timeline (vehicles on y-axis, trips as bars) for the selected plan.
- Chart: departure margin distribution.
- Summary metrics: trips served, unserved by priority, average margin, number of vehicles used.
- A "what changed" section listing trips whose assigned vehicle differs between baseline and optimized, with a one-line reason derived from numbers (for example, "higher departure SOC", "lower wear cost") where it can be computed; otherwise show only the change.

## Page 4 — Cost & Optimization Analytics

- Headline cards: baseline cost, optimized cost, savings (amount and %), peak kW reduction, kWh shifted out of peak. Cards show both numbers and the formula basis ("savings = baseline − optimized, same data and cost function").
- Waterfall or grouped bar chart: energy, demand, wear, penalties for each plan.
- Energy by tariff period (stacked bar) for each plan.
- Charger utilization by type and by hour.
- Solver panel: status, gap, runtime, variables, constraints, method used (MILP vs heuristic). If the fallback ran, show a visible notice.
- If trip sets differ between plans, show a warning that the plans are not directly comparable and present total cost with penalties.
- Sensitivity widget: choose a weight (wear, unavailability) or demand charge, rerun, and display the resulting trade-off curve (cached; clearly labeled as a rerun using modified config).

## Page 5 — Recommendations & Alerts

- Sort and filter by severity and category. Render each as a card with title, detail, evidence numbers, and affected vehicle or trip.
- Executive summary at the top (LLM-written only if enabled and verified, otherwise the deterministic summary). Show a small label when LLM text is used.
- Alerts panel: low SOC, binding site limit, critical trips at risk, unserved trips.
- Plan validator results: "0 hard violations" success banner or a table of violations.
- Export recommendations as CSV or JSON.

## Page 6 — Scenario Lab

- Controls: scenario (base, price spike, charger outage, heavy demand, low-battery fleet, site constraint), fleet size, site limit slider, price multiplier for peak hours, solver time limit, objective weights.
- "Run scenario" button: builds modified config and tables, runs the pipeline into a new `run_*` folder (not overwriting `latest` unless the user chooses), and shows a side-by-side KPI comparison with the base run.
- History table of previous runs (timestamp, scenario, total cost, savings, trips served), selectable to load.
- Optional data source switches (real weather, routing, tariff) shown only when keys or files are available. Show the source badge and fallback message when real data cannot be fetched.

## `charts.py` requirements

Pure functions returning `plotly.graph_objects.Figure`. Include at least: `soc_bar`, `demand_vs_supply`, `charging_gantt`, `price_band`, `site_load`, `vehicle_soc_trajectory`, `trip_timeline`, `cost_breakdown`, `energy_by_period`, `charger_utilization`, `margin_hist`, `tradeoff_curve`. Each accepts plain DataFrames/dicts so it can be tested without Streamlit. Handle empty input by returning a figure with a clear "No data" annotation.

## `data_access.py`

- `list_runs()`, `load_run(run_dir) -> RunData` (dataclass with all tables and JSON files),
- `latest_run_dir()`, safe handling of missing or partial files with friendly errors,
- Convert slot columns to timestamps for display.

## Quality requirements

- No computation of savings or costs inside the dashboard; only read from `kpis.json` and plan tables (aggregations for display are fine if they match the pipeline's values; add a test that asserts that).
- Accessible table and chart titles, axis labels with units, tooltips with units.
- Defensive code: wrap each page body in a function and show `st.error` plus details for unexpected exceptions rather than a stack trace.
- Do not use `localStorage`-style hacks; use `st.session_state`.
- Provide `docs/DASHBOARD.md` with screenshots instructions and a short user guide.

## Tests (`tests/test_dashboard.py`)

- Every chart builder returns a figure for normal and empty inputs.
- `data_access.load_run` loads a fixture run and fails gracefully for missing files.
- Use `streamlit.testing.v1.AppTest` to confirm `app.py` runs without exception with a fixture output folder and with an empty one.
- Check that KPI numbers displayed equal values in `kpis.json`.

## Acceptance

After `python scripts/run_pipeline.py`, `streamlit run src/dashboard/app.py` opens on the overview page, every page renders with real results, the Run button reproduces the results, and Scenario Lab runs the price-spike scenario and shows energy moving out of the spike window.
