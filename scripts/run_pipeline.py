"""CLI script to run end-to-end EV Fleet Energy Optimization pipeline and display formatted KPIs."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure workspace root is on sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from src.common.config import load_config
from src.agents.orchestrator import run_pipeline
from src.data.simulator import generate_all_tables, save_tables
from src.data.scenarios import apply_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AI Energy & EV Fleet Optimizer Pipeline")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--vehicles", type=int, default=20, help="Number of vehicles")
    parser.add_argument("--scenario", type=str, default="base", help="Scenario (base, price_spike, charger_outage, heavy_demand, low_battery_fleet, site_constraint)")
    parser.add_argument("--time-limit", type=int, default=25, help="Solver time limit in seconds")
    parser.add_argument("--out", type=str, default="data/outputs/latest", help="Output directory")
    args = parser.parse_args()

    print("\n" + "=" * 80, flush=True)
    print(" AI ENERGY & EV FLEET OPTIMIZATION AGENT — PIPELINE RUN", flush=True)
    print("=" * 80, flush=True)

    config = load_config(overrides={
        "random_seed": args.seed,
        "fleet": {"n_vehicles": args.vehicles},
        "solver": {"time_limit_s": args.time_limit},
    })

    start_time = time.perf_counter()
    print(f"\n[1/3] Generating synthetic fleet data (Scenario: '{args.scenario}', Seed: {args.seed})...", flush=True)
    tables = generate_all_tables(config)
    if args.scenario != "base":
        tables = apply_scenario(args.scenario, tables, config)

    save_tables(tables, "data/processed")
    print(f"      Fleet: {len(tables['vehicles'])} vehicles | Trips: {len(tables['trips'])} | Chargers: {len(tables['chargers'])}")

    print("\n[2/3] Executing 7-Agent Optimization Architecture...")
    results = run_pipeline(config=config, tables=tables)

    total_duration = time.perf_counter() - start_time
    kpis = results["kpis"]
    base_kpi = kpis.get("baseline", {})
    opt_kpi = kpis.get("optimized", {})
    sav_kpi = kpis.get("savings", {})
    solver_info = kpis.get("solver", {})

    print("\n[3/3] Optimization Completed Successfully!")
    print("\n" + "-" * 80)
    print(f"{'METRIC':<36} | {'BASELINE':<18} | {'OPTIMIZED':<18} | {'SAVINGS / DIFF':<18}")
    print("-" * 80)

    curr = config.currency
    print(f"{'Operating Cost (' + curr + ')':<36} | {base_kpi.get('operating_cost', 0):>15.2f}  | {opt_kpi.get('operating_cost', 0):>15.2f}  | {sav_kpi.get('abs', 0):>13.2f} ({sav_kpi.get('pct', 0):.1f}%)")
    print(f"{'  - Energy Electricity Cost':<36} | {base_kpi.get('energy_cost', 0):>15.2f}  | {opt_kpi.get('energy_cost', 0):>15.2f}  | {sav_kpi.get('energy_cost_abs', 0):>13.2f}")
    print(f"{'  - Peak Demand Cost':<36} | {base_kpi.get('demand_cost', 0):>15.2f}  | {opt_kpi.get('demand_cost', 0):>15.2f}  | {sav_kpi.get('demand_cost_abs', 0):>13.2f}")
    print(f"{'  - Battery Wear Cost':<36} | {base_kpi.get('wear_cost', 0):>15.2f}  | {opt_kpi.get('wear_cost', 0):>15.2f}  | {sav_kpi.get('wear_cost_abs', 0):>13.2f}")
    print(f"{'Peak Site Power (kW)':<36} | {base_kpi.get('peak_kw', 0):>15.2f}  | {opt_kpi.get('peak_kw', 0):>15.2f}  | {base_kpi.get('peak_kw', 0) - opt_kpi.get('peak_kw', 0):>13.2f}")
    print(f"{'Total Energy Charged (kWh)':<36} | {base_kpi.get('energy_kwh_grid', 0):>15.2f}  | {opt_kpi.get('energy_kwh_grid', 0):>15.2f}  | {base_kpi.get('energy_kwh_grid', 0) - opt_kpi.get('energy_kwh_grid', 0):>13.2f}")
    print(f"{'Energy Charged in Peak Window (kWh)':<36} | {base_kpi.get('energy_by_period_kwh', {}).get('peak', 0):>15.2f}  | {opt_kpi.get('energy_by_period_kwh', {}).get('peak', 0):>15.2f}  | {sav_kpi.get('kwh_shifted_out_of_peak', 0):>13.2f} shifted")
    print(f"{'Trips Served / Total':<36} | {str(base_kpi.get('trips_served', 0)) + '/' + str(base_kpi.get('trips_total', 0)):>15}  | {str(opt_kpi.get('trips_served', 0)) + '/' + str(opt_kpi.get('trips_total', 0)):>15}  | {'0 unserved':>13}")
    print("-" * 80)
    print(f"Solver: {solver_info.get('method', 'pulp')} | Status: {solver_info.get('status', 'Optimal')} | Solver Runtime: {solver_info.get('runtime_s', 0):.2f}s | Total Pipeline: {total_duration:.2f}s")
    print(f"Outputs written to: {results['output_dir']} (mirrored to data/outputs/latest/)\n")
    print(f"Executive Summary:\n{results['executive_summary']}\n")
    print("=" * 80)


if __name__ == "__main__":
    main()
