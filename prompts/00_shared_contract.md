# 00 — Shared Contract (single source of truth)

All other prompt files depend on this one. Implement these names, units and
schemas exactly so that the data, agents, optimizer and dashboard fit together.

## 1. Project structure

```
ev_fleet_optimizer/
├── AGENTS.md
├── README.md
├── requirements.txt
├── .env.example
├── config/settings.yaml
├── prompts/                      (these files)
├── docs/                         (BUILD_LOG.md, DECISIONS.md, ARCHITECTURE.md)
├── data/
│   ├── processed/                (simulated or imported input tables, CSV)
│   ├── external_cache/           (cached API responses)
│   └── outputs/
│       ├── latest/               (copy of most recent run)
│       └── run_YYYYMMDD_HHMMSS/
├── src/
│   ├── common/   config.py, schemas.py, time_grid.py, logging_utils.py, io.py
│   ├── data/     simulator.py, scenarios.py, external.py, loaders.py, validators.py
│   ├── agents/   base.py, fleet_agent.py, battery_agent.py, route_agent.py,
│   │             charging_agent.py, cost_agent.py, optimization_agent.py,
│   │             recommendation_agent.py, orchestrator.py
│   ├── optimization/ model.py, baseline.py, heuristic.py, evaluate.py,
│   │                 validator.py, postprocess.py, solver.py
│   └── dashboard/    app.py, data_access.py, charts.py, components.py,
│                     pages/ (01_overview.py ... 06_scenarios.py)
├── scripts/run_pipeline.py
└── tests/        test_data.py, test_agents.py, test_optimization.py, test_dashboard.py
```

Every package folder has an `__init__.py`. Run modules from the project root.

## 2. Units and conventions

- Energy: kWh. Power: kW. Distance: km. Temperature: °C. Time step: `slot_minutes` (default 15), so Δt = slot_minutes / 60 hours.
- SOC is stored as percent (0–100) in tables and converted to kWh inside the model: `energy_kwh = soc_pct/100 * battery_capacity_kwh * soh`.
- Money: configurable currency (`currency` in settings, default `INR`), tariff in currency per kWh.
- Time: planning horizon is a discrete grid of slots indexed `0 … n_slots-1`. Default start 18:00 on plan day, 96 slots, ending 18:00 next day (overnight depot charging). Convert between slot index and timestamp only through `src/common/time_grid.py`.
- All random generation uses `numpy.random.default_rng(seed)`. Same seed means identical data.
- Every input table has a `source` column: `simulated`, `real:<provider>` or `imported`.

## 3. config/settings.yaml (create with these keys and defaults)

```yaml
random_seed: 42
currency: INR
horizon:
  plan_date: "auto"        # "auto" = tomorrow's date
  start_time: "18:00"
  slot_minutes: 15
  n_slots: 96
fleet:
  n_vehicles: 20
  n_depots: 1
  vehicle_mix: {van_small: 0.4, van_large: 0.4, truck: 0.2}
chargers:
  ac: {count: 6, power_kw: 11.0, efficiency: 0.92}
  dc: {count: 2, power_kw: 30.0, efficiency: 0.94, derate_factor: 0.85}
site:
  limit_kw: 90.0
battery:
  reserve_soc_pct: 15         # never plan to go below this
  daily_ceiling_pct: 90       # normal charge ceiling for battery health
  high_soc_threshold_pct: 80  # dwelling above this adds wear cost
  end_of_horizon_target_pct: 60
trips:
  safety_margin: 0.10
  turnaround_slots: 2
tariff:
  offpeak: {hours: "22:00-06:00", price: 4.5}
  shoulder: {hours: "06:00-18:00", price: 7.0}
  peak: {hours: "18:00-22:00", price: 10.5}
  demand_charge_per_kw: 150.0   # applied once per plan to the peak site kW
  noise_pct: 3
costs:
  wear_cost_per_kwh_throughput: 0.8
  wear_cost_per_kwh_slot_above_threshold: 0.02
  unserved_trip_penalty: {1: 100000, 2: 30000, 3: 8000}   # by priority
  end_target_shortfall_penalty_per_kwh: 5.0
weights: {unavailability: 1.0, battery_wear: 1.0, violations: 1.0}
solver: {name: cbc, time_limit_s: 60, mip_gap: 0.01, threads: 4}
external: {use_real_weather: false, use_real_routing: false, use_real_tariff: false}
llm: {enabled: false, provider: gemini, model: "gemini-2.0-flash"}
```

`src/common/config.py` loads this into a typed dataclass tree and supports overrides by dict (used by dashboard scenarios).

## 4. Input table schemas (CSV in `data/processed/`)

**depots.csv**: `depot_id, name, site_limit_kw, lat, lon, source`

**chargers.csv**: `charger_id, depot_id, type (AC|DC), max_kw, efficiency, derate_factor, source`

**vehicles.csv**: `vehicle_id, model, depot_id, battery_capacity_kwh, soh (0.7–1.0), efficiency_kwh_per_km, payload_capacity_kg, max_ac_kw, max_dc_kw (0 if no DC), current_soc_pct, available_from_slot, status (available|maintenance|in_service), reserve_soc_pct, ceiling_soc_pct, source`

**trips.csv**: `trip_id, depot_id, departure_slot, return_slot, distance_km, payload_kg, temperature_c, priority (1 critical, 2 normal, 3 flexible), source`
Constraint: `departure_slot < return_slot <= n_slots`. Trips starting after the horizon are not generated.

**tariffs.csv**: `slot, timestamp, depot_id, price_per_kwh, period (offpeak|shoulder|peak), source`

**weather.csv**: `slot, timestamp, temperature_c, source`

**outages.csv** (optional scenario input): `charger_id, start_slot, end_slot`

## 5. Output tables (in `data/outputs/<run>/`)

- `charging_schedule.csv`: `vehicle_id, charger_id, charger_type, slot, timestamp, power_kw_grid, energy_to_battery_kwh, price_per_kwh, plan` where `plan` is `baseline` or `optimized`.
- `assignments.csv`: `trip_id, vehicle_id (blank if unserved), served (bool), required_energy_kwh, departure_soc_pct, plan`.
- `soc_trajectory.csv`: `vehicle_id, slot, timestamp, soc_pct, energy_kwh, on_trip (bool), plan`.
- `site_load.csv`: `slot, timestamp, site_kw, site_limit_kw, price_per_kwh, plan`.
- `kpis.json`: nested dict with keys `baseline`, `optimized`, `savings` (see prompt 03 for exact fields), plus `solver` (status, gap, runtime_s, objective).
- `recommendations.json`: list of `{id, severity (info|warning|critical), category, title, detail, vehicle_id?, trip_id?, evidence: {…numbers…}}`.
- `agent_log.jsonl`: one JSON per agent execution (agent, status, duration_s, warnings, metrics).
- `run_meta.json`: seed, config snapshot, data sources used, timestamps, code version if available.

## 6. Python interfaces (exact names)

```python
# src/agents/base.py
@dataclass
class AgentContext:
    config: Config
    tables: dict[str, pd.DataFrame]      # input tables by name
    results: dict[str, Any]              # outputs of earlier agents, keyed by agent name

@dataclass
class AgentResult:
    agent: str
    status: Literal["ok", "warning", "failed"]
    outputs: dict[str, Any]
    warnings: list[str]
    metrics: dict[str, float]
    duration_s: float

class BaseAgent(ABC):
    name: str
    def run(self, ctx: AgentContext) -> AgentResult: ...

# src/optimization/solver.py
def solve(opt_input: OptimizationInput, config: Config) -> Plan: ...

# src/optimization/baseline.py
def build_baseline_plan(opt_input: OptimizationInput, config: Config) -> Plan: ...

# src/optimization/evaluate.py   (ONE cost function used for both plans)
def evaluate_plan(plan: Plan, opt_input: OptimizationInput, config: Config) -> dict: ...

# src/optimization/validator.py
def validate_plan(plan: Plan, opt_input: OptimizationInput, config: Config) -> list[Violation]: ...

# src/agents/orchestrator.py
def run_pipeline(config: Config, tables: dict | None = None, output_dir: Path | None = None) -> dict: ...
```

`OptimizationInput` and `Plan` are dataclasses defined in `src/common/schemas.py`
(fields specified in prompts 02 and 03). `Plan` holds `charging` (DataFrame),
`assignments` (DataFrame), `soc` (DataFrame), `name` (`baseline` | `optimized`) and
`solver_info` (dict).

## 7. Quality bar

- Deterministic for a given seed and config.
- Pure functions where possible; no global mutable state.
- Each module has at least one test. Use small fixtures (e.g., 4 vehicles, 2 chargers, 48 slots) for fast tests.
- Logging through `logging`, not `print`, except in `scripts/run_pipeline.py` which prints a readable summary.
