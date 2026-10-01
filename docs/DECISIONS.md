# Design Decisions & Assumptions

1. **Single Source of Truth**: All units, schemas, and configurations follow `prompts/00_shared_contract.md`. If discrepancies arise, `00_shared_contract.md` takes precedence.
2. **Deterministic Simulation**: Synthetic data generation is completely reproducible using `numpy.random.default_rng(seed)`. Default seed is `42`.
3. **Solver Strategy**: PuLP with the bundled CBC solver is the primary MILP engine for zero-configuration, cross-platform execution.
4. **Charger Assignment Hierarchy**:
   - At the MILP stage, charger allocation is decided at the *type level* (`AC` vs `DC`) using binary decision variables $y_{v, g, t}$ and power variables $p_{v, g, t}$.
   - Postprocessing applies greedy interval coloring to map type-level assignments to concrete physical charger IDs (`CH-AC-01`, `CH-DC-01`, etc.), ensuring zero overlaps on physical hardware.
5. **Binary Variable Rigor**:
   - Connection indicators $y_{v, g, t}$ must be declared as `cat=pulp.LpBinary`. Continuous relaxation leads to fractional charger sharing in single slots, which would violate physical charger counts.
6. **Windows CBC Multithreading**:
   - CBC 2.10.3 on Windows deadlocks or hangs in the POSIX thread abstraction layer when thread parameters (`-threads N`) are supplied. On Windows (`sys.platform == 'win32'`), `threads` is automatically set to `None`, invoking CBC in pure single-threaded mode where time limits and signal traps function accurately.
7. **Initial Energy State & Variable Lower Bounds**:
   - Synthetic vehicles may initialize with battery state below the nominal reserve threshold (e.g., a vehicle arriving at 8% SOC when nominal reserve is 15%). Energy variables $e_{v, t}$ have a physical lower bound of `0.0` (rather than `e_min`), allowing the solver to charge vehicles up toward reserve levels without rendering the initial state $e_{v, 0} = e_{\text{init}}$ infeasible.
8. **Heuristic Fallback**:
   - A price-aware greedy heuristic is available if the MILP times out or encounters infeasibility, ensuring the orchestrator always returns an actionable plan with transparent status metadata (`method: heuristic_fallback`).
9. **Single Unified Cost Evaluator**:
   - Both baseline (unmanaged plug-in charging) and optimized plans are evaluated by the identical cost evaluation logic in `src/optimization/evaluate.py`. Savings are calculated strictly as `baseline_cost - optimized_cost` on identical inputs.
10. **External Integrations & Graceful Degradation**:
    - All external APIs (Open-Meteo weather, OSRM routing, ENTSO-E tariffs, Gemini LLM) are strictly optional. They fail gracefully back to synthetic data upon network error or missing credentials and cache responses under `data/external_cache/`. Every generated dataframe includes a `source` column (`simulated` vs `external`).
