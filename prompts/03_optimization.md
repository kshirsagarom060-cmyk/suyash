# 03 — Optimization Engine

Read `00_shared_contract.md` and `02_agent_modules.md` first. Implement under `src/optimization/`.

## Goal

Decide, for the planning horizon:
1. which vehicle serves each trip,
2. when and at what power each vehicle charges, on which charger type,
3. while minimizing electricity cost, peak demand cost and battery wear, and never violating hard operational constraints.

Use **PuLP with the CBC solver** as the primary engine (easy install on Windows). OR-Tools
(CP-SAT or its MILP wrapper) may be offered as an alternative behind `solver.name`, but CBC must work out of the box.

## Files

| File | Responsibility |
|---|---|
| `model.py` | Builds and solves the MILP; returns raw variable values. |
| `baseline.py` | "Plug in on arrival and charge immediately" policy plus greedy trip assignment. |
| `heuristic.py` | Fast price-aware greedy fallback when the MILP fails or times out. |
| `evaluate.py` | The single cost and KPI function used for every plan. |
| `validator.py` | Independent re-check of all hard constraints on any plan. |
| `postprocess.py` | Converts raw variables to `Plan` tables; assigns physical charger IDs. |
| `solver.py` | `solve()` orchestrating model, fallback, and post-processing. |

## Sets and parameters

- `V` usable vehicles, `J` trips, `T = 0..n_slots-1`, `G = {AC, DC}` charger types, `Δ = slot_hours`.
- `price[t]`, `site_limit[t]`, `N[g,t]` available chargers of type g, `P[v,g,t]` max grid-side power for vehicle v on type g (`min(charger effective kW, vehicle limit)`; 0 if incompatible), `η[g]` charger efficiency.
- Vehicle energy: `e_init[v], e_min[v], e_max[v], e_high[v], e_end[v], avail[v]`.
- Trip: `dep[j], ret[j]`; `E[v,j]` trip energy; `R[v,j] = E[v,j] + e_min[v]`; `elig[v,j]`.
- Wear and penalty coefficients from `cost_params`.

Modeling note: chargers of the same type are interchangeable, so model **type-level**
capacity (`Σ_v y[v,g,t] ≤ N[g,t]`) instead of one binary per physical charger. Afterwards,
`postprocess.py` assigns physical `charger_id`s by interval coloring (sort charging sessions
by start slot, give each the first free charger of that type). This keeps the binary count near
`|V|·|G|·|T|` (about 3,840 for the default case) so CBC solves in seconds to a minute.

## Decision variables

- `a[v,j] ∈ {0,1}` — vehicle v serves trip j (only created when `elig[v,j]`).
- `u[j] ∈ {0,1}` — trip j unserved.
- `y[v,g,t] ∈ {0,1}` — vehicle v charging on type g in slot t (only where `P[v,g,t] > 0`).
- `p[v,g,t] ≥ 0` — grid-side charging power in kW.
- `e[v,t] ≥ 0` for `t = 0..n_slots` — stored energy (kWh) at the start of slot t.
- `h[v,t] ≥ 0` — energy above the high-SOC threshold.
- `s_end[v] ≥ 0` — shortfall against the end-of-horizon target.
- `peak ≥ 0` — maximum site power.

## Constraints (all hard unless noted)

1. **Trip coverage (soft through `u`)**: `Σ_v a[v,j] + u[j] = 1` for every trip.
2. **No double booking**: for every vehicle and slot, `Σ_{j active at t} a[v,j] ≤ 1`. Trips closer than `turnaround_slots` count as overlapping (extend `ret[j]` by the turnaround).
3. **Away means no charging**: `Σ_g y[v,g,t] ≤ 1 − Σ_{j active at t} a[v,j]`. Also `y = 0` for `t < avail[v]`.
4. **One connection per vehicle**: `Σ_g y[v,g,t] ≤ 1`.
5. **Charger-type capacity**: `Σ_v y[v,g,t] ≤ N[g,t]`.
6. **Power bounds**: `p[v,g,t] ≤ P[v,g,t] · y[v,g,t]`. Optional minimum power `p ≥ 0.1·P·y` to avoid trickle sessions (config flag).
7. **Site limit**: `Σ_{v,g} p[v,g,t] ≤ site_limit[t]`.
8. **Peak definition**: `peak ≥ Σ_{v,g} p[v,g,t]` for all t.
9. **Energy dynamics**:
   `e[v,t+1] = e[v,t] + Δ · Σ_g η[g] · p[v,g,t] − consumed[v,t]`, with `consumed[v,t] = Σ_j a[v,j] · E[v,j]` when `t = ret[j] − 1` (energy is deducted when the trip ends), and `e[v,0] = e_init[v]`.
10. **Battery limits**: `e_min[v] ≤ e[v,t] ≤ e_max[v]` for all t. Energy dips from trips do not violate this because constraint 11 requires enough energy at departure.
11. **Enough energy to depart**: `e[v,dep[j]] ≥ R[v,j] · a[v,j]` for every eligible pair. This is linear since `R` is a constant.
12. **High-SOC dwell**: `h[v,t] ≥ e[v,t] − e_high[v]`, `h ≥ 0`.
13. **End-of-horizon readiness (soft)**: `e[v,n_slots] + s_end[v] ≥ e_end[v]`.
14. **Eligibility**: pairs with `elig = False` have no variable (equivalent to `a = 0`).

The model must keep hard constraints hard. The only slack variables are `u[j]` (unserved trips) and `s_end[v]`.

## Objective

```
min  Σ_t price[t] · Δ · Σ_{v,g} p[v,g,t]                                  # energy cost
   + demand_charge · peak                                                  # peak demand cost
   + w_wear · ( wear_per_kwh[v] · Δ · Σ p  +  wear_high · Δ · Σ_{v,t} h[v,t] )   # battery wear
   + w_unavail · Σ_j penalty[priority_j] · u[j]                            # unserved trips
   + w_viol · end_shortfall_penalty · Σ_v s_end[v]                         # end readiness
```

Weights come from config. Unserved-trip penalties must dominate everything else so the solver
never drops a trip to save money unless it is physically impossible. Include a tiny tie-breaker
(e.g., `1e-4 · Σ t · p`) only if solutions look erratic, and document it.

## Baseline policy (`baseline.py`)

A realistic "do nothing smart" operation, used to measure savings:
- **Trip assignment**: process trips by departure time; assign the eligible, free vehicle with the highest energy at departure; on ties pick lowest vehicle ID. Unserved if none.
- **Charging**: each vehicle plugs in at `avail[v]` and charges at full available power until it reaches `e_max` (or the baseline target set in config, default `ceiling_soc_pct`), first-come-first-served on the first free compatible charger. When site power would be exceeded, curtail later vehicles for that slot. No consideration of price.
- Output a normal `Plan`; run through the same validator and `evaluate_plan`. Baseline may legitimately violate things (late charge, unserved trip); report that, do not hide it.

## Heuristic fallback (`heuristic.py`)

Used when CBC returns infeasible, errors, or no incumbent by the time limit:
1. Assign trips by earliest deadline and priority using eligible vehicles with the most surplus energy.
2. For each vehicle, compute required energy before departure and before the horizon end.
3. Sort the allowed slots before each departure by price; fill the cheapest slots first, respecting charger-type counts and site limit slot by slot (most urgent vehicles first).
4. Re-validate; return a `Plan` with `solver_info.method = "heuristic"`.

## Evaluation (`evaluate.py`)

`evaluate_plan(plan, opt_input, config) -> dict` returns, for any plan:

```json
{
  "energy_kwh_grid": 0.0, "energy_cost": 0.0, "demand_cost": 0.0, "wear_cost": 0.0,
  "unserved_penalty": 0.0, "end_shortfall_kwh": 0.0, "total_cost": 0.0,
  "operating_cost": 0.0,                     
  "peak_kw": 0.0, "avg_price_paid": 0.0,
  "trips_total": 0, "trips_served": 0, "trips_unserved": 0,
  "min_departure_margin_kwh": 0.0,
  "charger_utilization": {"AC": 0.0, "DC": 0.0},
  "energy_by_period_kwh": {"offpeak": 0.0, "shoulder": 0.0, "peak": 0.0},
  "peak_period_share_pct": 0.0,
  "high_soc_vehicle_hours": 0.0
}
```

`operating_cost = energy_cost + demand_cost + wear_cost` (real money-like cost, excluding penalties). The
dashboard's headline "savings" uses `operating_cost` when both plans serve the same trips; if the
plans serve different trip sets, show total cost and flag the difference explicitly.
`kpis.json` = `{baseline: {...}, optimized: {...}, savings: {abs, pct, energy_cost_abs, demand_cost_abs, wear_cost_abs, kwh_shifted_out_of_peak}, solver: {...}}`.
Compute everything from the plan tables; never reuse solver objective values as reported savings.

## Validator (`validator.py`)

`validate_plan` independently recomputes SOC trajectories from the charging table and checks every
hard constraint above, returning a list of `Violation(type, vehicle_id, trip_id, slot, amount, message)`.
Checks include: double-booked vehicle, charging while on trip, charging before availability, charger count
per type, vehicle power limits, site limit, SOC bounds, enough energy at departure, eligibility, and consistency
of the SOC table with the charging table. It must not import the model. The optimized plan must produce an empty list in the base scenario.

## Solver wrapper (`solver.py`)

```python
def solve(opt_input, config) -> Plan:
    # 1. Build model (warm-start from baseline values if supported).
    # 2. Solve with time_limit_s, mip_gap, threads from config.
    # 3. Map status: Optimal / Feasible (time limit) -> use. Infeasible / NotSolved -> heuristic.
    # 4. Post-process, validate, and return Plan with solver_info:
    #    {status, objective, best_bound, gap, runtime_s, n_variables, n_constraints, method}
```

Log model size before solving. If the instance exceeds a size threshold (e.g., more than 15,000 binaries), automatically coarsen to 30-minute slots and expand the result back, or warn clearly.

Provide `explain_infeasibility(opt_input)` that identifies trips with no eligible vehicle, time windows where required energy exceeds deliverable energy, and site-limit bottlenecks. The recommendation agent uses it.

## Tests (`tests/test_optimization.py`)

Small fixtures (4 vehicles, 3 trips, 1 AC + 1 DC charger, 48 slots):
- Optimized plan passes the validator with zero violations.
- Optimized `operating_cost` ≤ baseline `operating_cost` in a scenario where the baseline charges in peak hours.
- With prices flat, optimizer cost ≈ baseline cost (no phantom savings).
- Price spike scenario moves energy out of the spike window.
- Tight site limit: solver still serves all critical trips or reports exactly which cannot be served.
- A trip no vehicle can do ends up in `unserved` and the solver stays feasible.
- Charger-count constraint holds after physical charger assignment.
- Heuristic fallback returns a validator-clean plan on the base fixture.
- Validator catches deliberately corrupted plans (charge while away, over-limit site power, SOC below reserve).
- Determinism: same input gives same objective value.

## Acceptance

`python scripts/run_pipeline.py` prints a readable table of baseline vs optimized cost, peak kW, trips served,
energy shifted out of peak, solver status and runtime, and finishes in under 90 seconds on the default scenario.
