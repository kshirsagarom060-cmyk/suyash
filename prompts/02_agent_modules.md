# 02 — Agent Modules

Read `00_shared_contract.md` first. Implement under `src/agents/`.

## Design principle

The "agents" are specialized, independently testable Python modules that cooperate
through a shared `AgentContext`. Each agent reads inputs, does one job, and returns an
`AgentResult`. Numeric reasoning is deterministic. An LLM is optional and used **only**
to phrase explanations; it never produces numbers.

Pipeline order (the orchestrator enforces it):

```
Fleet → Battery → Route → Charging → Cost → Optimization → Recommendation
```

Each agent stores its outputs in `ctx.results[agent_name]` so later agents can read them.

## Shared base (`base.py`)

Implement `AgentContext`, `AgentResult`, `BaseAgent` exactly as in the contract. `BaseAgent`
provides a `run()` template that times execution, catches exceptions (status `failed`,
message in warnings), and appends a record to the agent log. Subclasses implement `_execute(ctx) -> AgentResult`.

Also in `src/common/schemas.py` define:

```python
@dataclass
class OptimizationInput:
    vehicles: pd.DataFrame      # per vehicle: id, usable capacity kWh, e_min, e_max, e_init, e_end_target,
                                # available_from_slot, max_ac_kw, max_dc_kw, depot_id
    trips: pd.DataFrame         # trip_id, departure_slot, return_slot, priority, payload_kg, depot_id
    eligibility: pd.DataFrame   # vehicle_id, trip_id, eligible (bool), energy_kwh, required_departure_kwh
    chargers: pd.DataFrame      # charger_id, type, max_kw, efficiency, derate_factor
    charger_capacity: pd.DataFrame  # slot, type, available_count, effective_kw (per charger of that type)
    site_capacity: pd.DataFrame     # slot, site_limit_kw
    prices: pd.DataFrame        # slot, price_per_kwh
    cost_params: dict           # demand charge, wear coefficients, penalties, weights
    n_slots: int
    slot_hours: float
```

## 1. FleetAgent (`fleet_agent.py`)

**Responsibility**: know which vehicles can work and which trips need serving.

Inputs: `vehicles`, `trips`, config.

Logic:
- Mark vehicles with `status != available` as unusable (excluded from `OptimizationInput`).
- Compute each vehicle's availability window (`available_from_slot` to horizon end).
- Build the **eligibility skeleton**: for every usable vehicle and trip, `eligible = payload fits AND same depot`. Record a `reason` when false (`payload`, `depot`, `unavailable`).
- Detect time-overlap potential: for each slot, trips in progress vs usable vehicles; warn if demand exceeds supply.
- Trip demand summary by hour (count, total distance, priorities).

Outputs: `usable_vehicles`, `eligibility_base`, `demand_by_hour`, `fleet_summary` (total, usable, in maintenance, trips, trips by priority).

Warnings: trips with zero eligible vehicles; peak concurrent demand above supply.

## 2. BatteryAgent (`battery_agent.py`)

**Responsibility**: translate each vehicle's battery into energy limits and health costs.

Per vehicle compute:
- `usable_capacity_kwh = battery_capacity_kwh * soh`
- `e_init = current_soc_pct/100 * usable_capacity_kwh`
- `e_min = reserve_soc_pct/100 * usable_capacity_kwh`
- `e_max = ceiling_soc_pct/100 * usable_capacity_kwh`
- `e_high = high_soc_threshold_pct/100 * usable_capacity_kwh`
- `e_end_target = end_of_horizon_target_pct/100 * usable_capacity_kwh` (capped by `e_max`)
- `wear_cost_per_kwh` = base wear cost scaled by `(1/soh)` so older batteries cost more to cycle.
- Charging power limits per vehicle: `max_ac_kw`, `max_dc_kw`.

Alerts (list of dicts): vehicle below reserve, vehicle at risk of not reaching required SOC (cross-check later with Route output), state of health below 0.8.

Outputs: `battery_table` (one row per vehicle with the fields above), `battery_alerts`.

## 3. RouteAgent (`route_agent.py`)

**Responsibility**: energy required by each trip for each candidate vehicle.

Formula:

```
energy_kwh(v, j) = distance_km_j * efficiency_v * temp_factor(T_j) * payload_factor(v, j) * (1 + safety_margin)
temp_factor(T)   = 1 + 0.012 * max(0, 18 - T) + 0.008 * max(0, T - 28)    # heating / cooling penalty
payload_factor   = 1 + 0.15 * payload_kg_j / payload_capacity_kg_v
required_departure_kwh(v, j) = energy_kwh(v, j) + e_min_v
```

Mark `eligible = False` with reason `range` when `required_departure_kwh > e_max_v`
(the vehicle cannot do the trip even on a full permitted charge). Combine with
`eligibility_base` from the Fleet Agent.

Outputs: `eligibility` (full table with `energy_kwh`, `required_departure_kwh`, `eligible`, `reason`), `trip_energy_summary`.

Warnings: trips left with no eligible vehicle after range check; trips whose best case needs above 85% of a vehicle's capacity.

If real routing distances exist in trips (source starts with `real:`), use them unchanged.

## 4. ChargingAgent (`charging_agent.py`)

**Responsibility**: when and how much charging capacity exists.

- `charger_capacity` by `(slot, type)`: `available_count` = chargers of that type minus any in `outages.csv` during the slot; `effective_kw = max_kw * derate_factor` (DC derate models taper; AC derate = 1.0).
- Vehicle-charger compatibility: DC only if `max_dc_kw > 0`. Effective vehicle power on type = `min(effective_kw, vehicle limit)`.
- `site_capacity` by slot from site limit (allow time-varying limits via scenarios).
- Capacity summary: theoretical max energy deliverable per hour vs total energy needed (fleet deficit to `e_end_target` plus trip energy). Warn when needed energy exceeds deliverable energy before the earliest critical departure.

Outputs: `charger_capacity`, `site_capacity`, `compatibility` (vehicle_id, type, max_kw), `capacity_summary`.

## 5. CostAgent (`cost_agent.py`)

**Responsibility**: all money and wear parameters, and ownership of the evaluation.

- Price vector per slot (per depot), period labels, demand charge per kW.
- `cost_params`: wear coefficients, unserved-trip penalties by priority, end-target shortfall penalty, objective weights from config.
- Provide `evaluate(plan, opt_input)` by delegating to `src.optimization.evaluate.evaluate_plan` (single implementation of cost).
- Price insights: cheapest contiguous windows, peak windows, price spread; used by recommendations.

Outputs: `prices`, `cost_params`, `price_insights`.

## 6. OptimizationAgent (`optimization_agent.py`)

**Responsibility**: assemble `OptimizationInput`, run baseline and optimizer, validate.

Steps:
1. Build `OptimizationInput` from earlier agents' outputs.
2. `baseline = build_baseline_plan(...)` and `optimized = solve(...)`.
3. `validate_plan` on both (baseline violations are reported, not fatal; optimized violations are an error unless only soft penalties are involved).
4. `evaluate_plan` for both; compute savings.
5. Return plans, KPIs and solver info. If the solver fails or times out without an incumbent, use the heuristic fallback and set status `warning`.

Outputs: `opt_input`, `baseline_plan`, `optimized_plan`, `kpis`, `violations`.

## 7. RecommendationAgent (`recommendation_agent.py`)

**Responsibility**: turn results into ranked, evidence-backed advice and alerts.

Rule engine (each rule yields a recommendation with an `evidence` dict of real numbers):
- **Critical**: unserved priority-1 trip; vehicle assigned with margin below 5% of capacity; capacity deficit before a critical departure.
- **Warning**: site limit binding for more than N slots; charger utilization above 90%; vehicle below reserve; battery state of health below 0.8; large share of energy forced into peak hours.
- **Info**: energy shifted from peak to offpeak (kWh and %); estimated savings (amount and %); unused charger capacity; suggestion to add or move a charger when the solver shows persistent site-limit binding; suggestions for specific vehicles to swap trips.

Rank by severity, then by estimated monetary impact. Limit to 15 items.

Optional LLM narrative (if `llm.enabled` and the API key env var is present): send only the structured recommendations JSON; ask for a 150-word plain-English summary; instruct it not to add numbers. Verify every number in the response appears in the evidence; otherwise discard the text and use the deterministic template summary. Never block the pipeline on LLM errors.

Outputs: `recommendations` (list per contract), `executive_summary` (string), `llm_used` (bool).

## Orchestrator (`orchestrator.py`)

`run_pipeline(config, tables=None, output_dir=None) -> dict`:
- Load tables from `data/processed/` if not provided; generate them if missing.
- Validate tables, then run the seven agents in order, stopping on a `failed` result of Fleet, Battery, Route, Charging or Cost; Optimization failure triggers the heuristic fallback; Recommendation failure only drops recommendations.
- Write all outputs listed in the contract to a timestamped run folder, then copy to `data/outputs/latest/`.
- Return `{"kpis": ..., "plans": {...}, "recommendations": [...], "agent_log": [...], "output_dir": ...}`.
- Log each agent's start, end, duration, and warnings; write `agent_log.jsonl`.

## Tests (`tests/test_agents.py`)

Use a small fixture (4 vehicles, 3 trips, 2 chargers, 48 slots):
- FleetAgent excludes maintenance vehicles and flags payload mismatches.
- BatteryAgent energy values match hand calculations (include one explicit numeric check).
- RouteAgent: energy increases in cold weather and with heavier payload; range-ineligible vehicles flagged.
- ChargingAgent: outages reduce `available_count`; DC-incompatible vehicles excluded from DC.
- CostAgent: price vector length equals `n_slots`.
- RecommendationAgent: unserved critical trip produces a critical item whose evidence matches the plan.
- Orchestrator: end-to-end run writes all contract files; a failing agent yields a clear error and a partial log.
