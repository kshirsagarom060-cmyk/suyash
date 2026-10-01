# 01 — Data Simulation (and optional real-data adapters)

Read `00_shared_contract.md` first. Implement everything here under `src/data/`.

## Goal

Generate a realistic, reproducible, **feasible** overnight-charging scenario for an EV
fleet, and optionally replace parts of it with real data. The default run must need
no API keys and no internet.

## Files to implement

| File | Responsibility |
|---|---|
| `src/data/simulator.py` | Generates all input tables and writes CSVs. CLI entry point. |
| `src/data/scenarios.py` | Scenario modifiers applied on top of base data. |
| `src/data/external.py` | Optional real-data adapters with caching and fallback. |
| `src/data/loaders.py` | `load_tables(dir) -> dict[str, DataFrame]` with dtype enforcement. |
| `src/data/validators.py` | `validate_tables(tables, config) -> list[str]` (raise on fatal errors). |

CLI:

```
python -m src.data.simulator --seed 42 --vehicles 20 --out data/processed --scenario base
```

Flags override config values. Print a short summary (counts, tariff range, total trip energy).

## Vehicle model catalog (hard-code in `simulator.py` as a constant, mix from config)

| model | battery kWh | kWh/km | payload kg | max AC kW | max DC kW |
|---|---|---|---|---|---|
| van_small | 40 | 0.16 | 600 | 7.4 | 50 |
| van_large | 70 | 0.24 | 1200 | 11 | 50 |
| truck | 120 | 0.55 | 3500 | 11 | 80 |

Per vehicle, add noise: `soh ~ U(0.85, 1.0)`, efficiency ×`N(1, 0.04)`. About 5% of vehicles
have `status = maintenance` (not usable), none if the fleet has fewer than 10 vehicles.
Roughly one in four trucks and none of the small vans may lack DC (`max_dc_kw = 0`) to
exercise compatibility logic.

## Initial state

- `current_soc_pct ~ clip(N(35, 15), 8, 85)` (vehicles return from the day with low charge).
- `available_from_slot`: 70% of vehicles are 0 (already at depot); the rest are `U{1, 12}` slots (still returning).
- `reserve_soc_pct` and `ceiling_soc_pct` from config battery settings.

## Trip generation

- Total trips ≈ 0.85 × usable vehicles, plus 10% extra flexible trips.
- Departure times: 70% morning wave centered 07:00 (σ = 1 h), 20% midday, 10% evening start (19:00–23:00). Convert to slots; drop trips that would not fit the horizon.
- Duration: 2–9 hours depending on distance and speed (`avg_speed_kmh ~ U(25, 45)`), `return_slot = departure_slot + ceil(distance / speed * 60 / slot_minutes)`, capped at `n_slots`.
- `distance_km ~ lognormal` clipped to 15–220 km (heavier trips for trucks via payload).
- `payload_kg` drawn up to 90% of the largest payload in the fleet; trips needing more than any vehicle carries must not exist.
- `priority`: 20% priority 1, 55% priority 2, 25% priority 3.
- `temperature_c` from the weather table at departure.

**Feasibility guarantee**: after generation, run a repair loop. For every trip, at least one
vehicle must satisfy (a) payload fits, (b) a full charge to `ceiling_soc_pct` covers trip
energy plus reserve and safety margin. If not, shorten the trip or lower payload. Also
ensure the number of simultaneous trips never exceeds the usable vehicle count. The base
scenario must always be solvable; stress scenarios may be tight but should not be impossible.

## Tariff and weather

- Time-of-use prices from config (`offpeak`, `shoulder`, `peak`), multiplied by `1 + N(0, noise_pct/100)` per slot, floor at 50% of the base price. Handle windows that wrap midnight (e.g., `22:00-06:00`).
- Output `period` per slot for dashboard coloring.
- Weather: diurnal sine curve, base 24 °C ±8 °C, plus `N(0, 1)` noise (use your own configurable base). Efficiency adjustment itself belongs to the Route Agent, not here.

## Chargers and depots

- Create chargers from config counts (`AC`, `DC`) with ids `CH-AC-01`, `CH-DC-01`, etc.
- Single depot by default; if `n_depots > 1`, split vehicles, chargers and trips by depot and scale `site_limit_kw` proportionally.
- Default numbers deliberately make the site limit bind: total charger capacity should exceed `site_limit_kw` (6×11 + 2×30 = 126 kW vs 90 kW).

## Scenario modifiers (`scenarios.py`)

Each scenario is a function `(tables, config, rng) -> tables`:

- `base`: no change.
- `price_spike`: multiply prices between 22:00 and 02:00 by 2.5 (shows load shifting).
- `charger_outage`: write `outages.csv` disabling 2 AC chargers for 6 hours and one DC for 4 hours.
- `heavy_demand`: +30% trips, with more priority-1 trips.
- `low_battery_fleet`: initial SOC mean 20%.
- `site_constraint`: `site_limit_kw` reduced by 35%.

Expose `SCENARIOS: dict[str, Callable]` and `apply_scenario(name, ...)`. The dashboard calls these.

## Optional real-data adapters (`external.py`)

Each adapter returns a DataFrame in the exact schema above with `source = "real:<provider>"`,
caches raw responses to `data/external_cache/` (JSON, keyed by request hash, with a TTL),
uses `requests` with a timeout of 10 s, retries twice, and **falls back to simulated data
with a logged warning** on any failure. No adapter may crash the pipeline.

1. **Weather — Open-Meteo** (no key): hourly `temperature_2m` for `lat, lon`, interpolated to slots.
2. **Routing — OSRM public demo server or OpenRouteService** (`ORS_API_KEY` in `.env`, optional): replace `distance_km` for trips that have origin and destination coordinates. If trips have no coordinates, skip this adapter and say so in the log.
3. **Tariff — importer**: `load_tariff_csv(path)` reading a user-supplied tariff file (`timestamp, price_per_kwh`), resampled to the slot grid. Optionally an ENTSO-E day-ahead price adapter using `ENTSOE_API_KEY` if present. Real tariffs are never guessed; if missing, use the simulated tariff.
4. **Fleet telemetry — importer**: `import_vehicles_csv(path)` mapping user columns to the vehicles schema with a column-mapping dict and clear errors on missing columns.

Add a `DataSourceReport` (dict) listing which tables are simulated vs real, saved to `run_meta.json` and shown in the dashboard.

## Validation (`validators.py`)

Fail with clear messages when: duplicate IDs, negative energy or distance, `departure_slot >= return_slot`,
SOC outside 0–100, `reserve_soc_pct >= ceiling_soc_pct`, tariff rows missing for any slot, trips referencing unknown depots,
payload larger than every vehicle's capacity, or a trip with no feasible vehicle (warning in non-base scenarios).

## Tests (`tests/test_data.py`)

- Same seed produces identical CSVs; different seeds differ.
- Schemas and dtypes match the contract; slot ranges valid.
- Tariff covers all slots and contains offpeak, shoulder and peak periods.
- Every trip in the base scenario has at least one eligible vehicle.
- Each scenario runs and still passes validation (impossible-trip warnings allowed for stress scenarios).
- External adapters fall back cleanly when the network is mocked to fail.

## Acceptance

`python -m src.data.simulator --seed 42` writes all CSVs to `data/processed/`, prints a summary and exits 0 in under 5 seconds.
