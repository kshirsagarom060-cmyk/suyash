"""Synthetic data generator for EV fleet energy optimization."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Optional
import numpy as np
import pandas as pd

from src.common.config import Config, load_config
from src.common.time_grid import get_plan_start_datetime, slot_to_datetime, parse_time_window_slots
from src.data.validators import validate_tables

VEHICLE_CATALOG = {
    "van_small": {
        "battery_capacity_kwh": 40.0,
        "efficiency_kwh_per_km": 0.16,
        "payload_capacity_kg": 600.0,
        "max_ac_kw": 7.4,
        "max_dc_kw": 50.0,
    },
    "van_large": {
        "battery_capacity_kwh": 70.0,
        "efficiency_kwh_per_km": 0.24,
        "payload_capacity_kg": 1200.0,
        "max_ac_kw": 11.0,
        "max_dc_kw": 50.0,
    },
    "truck": {
        "battery_capacity_kwh": 120.0,
        "efficiency_kwh_per_km": 0.55,
        "payload_capacity_kg": 3500.0,
        "max_ac_kw": 11.0,
        "max_dc_kw": 80.0,
    },
}


def generate_depots(config: Config) -> pd.DataFrame:
    """Generates depots table."""
    n_depots = config.fleet.n_depots
    rows = []
    base_lat, base_lon = 28.6139, 77.2090  # New Delhi
    for i in range(1, n_depots + 1):
        depot_id = f"DEPOT-{i:02d}"
        rows.append({
            "depot_id": depot_id,
            "name": f"Depot {i}",
            "site_limit_kw": config.site.limit_kw,
            "lat": base_lat + (i - 1) * 0.05,
            "lon": base_lon + (i - 1) * 0.05,
            "source": "simulated",
        })
    return pd.DataFrame(rows)


def generate_chargers(config: Config, depots_df: pd.DataFrame) -> pd.DataFrame:
    """Generates chargers table."""
    rows = []
    depot_ids = depots_df["depot_id"].tolist()
    ac_count = config.chargers.ac.count
    dc_count = config.chargers.dc.count

    for i in range(1, ac_count + 1):
        d_id = depot_ids[(i - 1) % len(depot_ids)]
        rows.append({
            "charger_id": f"CH-AC-{i:02d}",
            "depot_id": d_id,
            "type": "AC",
            "max_kw": config.chargers.ac.power_kw,
            "efficiency": config.chargers.ac.efficiency,
            "derate_factor": config.chargers.ac.derate_factor,
            "source": "simulated",
        })

    for i in range(1, dc_count + 1):
        d_id = depot_ids[(i - 1) % len(depot_ids)]
        rows.append({
            "charger_id": f"CH-DC-{i:02d}",
            "depot_id": d_id,
            "type": "DC",
            "max_kw": config.chargers.dc.power_kw,
            "efficiency": config.chargers.dc.efficiency,
            "derate_factor": config.chargers.dc.derate_factor,
            "source": "simulated",
        })
    return pd.DataFrame(rows)


def generate_vehicles(config: Config, depots_df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Generates fleet vehicles table."""
    n_vehicles = config.fleet.n_vehicles
    depot_ids = depots_df["depot_id"].tolist()
    mix = config.fleet.vehicle_mix
    models = list(mix.keys())
    probs = [mix[m] for m in models]
    probs = [p / sum(probs) for p in probs]

    rows = []
    n_maint = int(round(0.05 * n_vehicles)) if n_vehicles >= 10 else 0
    maint_indices = set(rng.choice(n_vehicles, size=n_maint, replace=False)) if n_maint > 0 else set()

    truck_dc_count = 0
    for idx in range(n_vehicles):
        v_id = f"EV-{idx+1:03d}"
        model = rng.choice(models, p=probs)
        spec = VEHICLE_CATALOG[model]
        depot_id = depot_ids[idx % len(depot_ids)]

        soh = float(np.round(rng.uniform(0.85, 1.0), 3))
        eff_noise = float(rng.normal(1.0, 0.04))
        eff = float(np.round(spec["efficiency_kwh_per_km"] * max(0.8, eff_noise), 3))

        max_dc = spec["max_dc_kw"]
        if model == "truck":
            truck_dc_count += 1
            if truck_dc_count % 4 == 0:
                max_dc = 0.0  # 1 in 4 trucks lacks DC

        soc_val = float(np.clip(rng.normal(35, 15), 8, 85))
        is_delayed = (rng.uniform() > 0.70)
        avail_slot = int(rng.integers(1, 13)) if is_delayed else 0

        status = "maintenance" if idx in maint_indices else "available"

        rows.append({
            "vehicle_id": v_id,
            "model": model,
            "depot_id": depot_id,
            "battery_capacity_kwh": spec["battery_capacity_kwh"],
            "soh": soh,
            "efficiency_kwh_per_km": eff,
            "payload_capacity_kg": spec["payload_capacity_kg"],
            "max_ac_kw": spec["max_ac_kw"],
            "max_dc_kw": max_dc,
            "current_soc_pct": round(soc_val, 1),
            "available_from_slot": avail_slot,
            "status": status,
            "reserve_soc_pct": config.battery.reserve_soc_pct,
            "ceiling_soc_pct": config.battery.daily_ceiling_pct,
            "source": "simulated",
        })
    return pd.DataFrame(rows)


def generate_weather(config: Config, rng: np.random.Generator) -> pd.DataFrame:
    """Generates weather table with diurnal temperature cycle."""
    n_slots = config.horizon.n_slots
    start_dt = get_plan_start_datetime(config.horizon.plan_date, config.horizon.start_time)
    rows = []
    for s in range(n_slots):
        slot_dt = slot_to_datetime(s, start_dt, config.horizon.slot_minutes)
        # 14:00 is warmest (~slot 32 from 18:00), 05:00 coolest (~slot 44)
        angle = 2 * math.pi * (s - 32) / 96.0
        temp = 24.0 - 8.0 * math.cos(angle) + float(rng.normal(0, 1.0))
        rows.append({
            "slot": s,
            "timestamp": slot_dt.strftime("%Y-%m-%d %H:%M"),
            "temperature_c": round(temp, 1),
            "source": "simulated",
        })
    return pd.DataFrame(rows)


def generate_tariffs(config: Config, depots_df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Generates tariffs table for all depots and slots."""
    n_slots = config.horizon.n_slots
    start_dt = get_plan_start_datetime(config.horizon.plan_date, config.horizon.start_time)
    slot_minutes = config.horizon.slot_minutes
    depot_ids = depots_df["depot_id"].tolist()

    offpeak_slots = set(parse_time_window_slots(config.tariff.offpeak.hours, start_dt, slot_minutes, n_slots))
    peak_slots = set(parse_time_window_slots(config.tariff.peak.hours, start_dt, slot_minutes, n_slots))

    rows = []
    for d_id in depot_ids:
        for s in range(n_slots):
            slot_dt = slot_to_datetime(s, start_dt, slot_minutes)
            if s in peak_slots:
                period = "peak"
                base_price = config.tariff.peak.price
            elif s in offpeak_slots:
                period = "offpeak"
                base_price = config.tariff.offpeak.price
            else:
                period = "shoulder"
                base_price = config.tariff.shoulder.price

            noise = float(rng.normal(0, config.tariff.noise_pct / 100.0))
            price = max(0.5 * base_price, base_price * (1.0 + noise))
            rows.append({
                "slot": s,
                "timestamp": slot_dt.strftime("%Y-%m-%d %H:%M"),
                "depot_id": d_id,
                "price_per_kwh": round(price, 3),
                "period": period,
                "source": "simulated",
            })
    return pd.DataFrame(rows)


def generate_trips(
    config: Config,
    vehicles_df: pd.DataFrame,
    weather_df: pd.DataFrame,
    depots_df: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generates feasible trips with departure waves and repair loop."""
    usable_vehicles = vehicles_df[vehicles_df["status"] == "available"]
    n_usable = len(usable_vehicles)
    if n_usable == 0:
        return pd.DataFrame(columns=["trip_id", "depot_id", "departure_slot", "return_slot", "distance_km", "payload_kg", "temperature_c", "priority", "source"])

    n_trips = max(1, int(round(0.85 * n_usable + 0.10 * n_usable)))
    n_slots = config.horizon.n_slots
    slot_minutes = config.horizon.slot_minutes
    max_fleet_payload = usable_vehicles["payload_capacity_kg"].max()

    depot_ids = depots_df["depot_id"].tolist()
    weather_map = dict(zip(weather_df["slot"], weather_df["temperature_c"]))

    raw_trips = []
    for i in range(1, n_trips + 1):
        trip_id = f"TRIP-{i:03d}"
        depot_id = depot_ids[(i - 1) % len(depot_ids)]

        # Departure wave: 70% morning (slot ~52 from 18:00), 20% midday (slots 68-80), 10% evening (slots 4-20)
        roll = rng.uniform()
        if roll < 0.70:
            dep_slot = int(np.clip(rng.normal(52, 4), 40, 64))
        elif roll < 0.90:
            dep_slot = int(rng.integers(65, 78))
        else:
            dep_slot = int(rng.integers(4, 20))

        dist = float(np.clip(rng.lognormal(mean=3.8, sigma=0.6), 15.0, 180.0))
        speed = float(rng.uniform(25.0, 45.0))
        dur_min = (dist / speed) * 60.0
        dur_slots = max(8, int(math.ceil(dur_min / slot_minutes)))
        dur_slots = min(dur_slots, 36)

        ret_slot = min(n_slots, dep_slot + dur_slots)
        if dep_slot >= ret_slot:
            dep_slot = max(0, ret_slot - 8)

        payload = float(np.round(rng.uniform(100.0, 0.90 * max_fleet_payload), 0))

        pri_roll = rng.uniform()
        if pri_roll < 0.20:
            priority = 1
        elif pri_roll < 0.75:
            priority = 2
        else:
            priority = 3

        temp_c = float(weather_map.get(dep_slot, 24.0))

        raw_trips.append({
            "trip_id": trip_id,
            "depot_id": depot_id,
            "departure_slot": dep_slot,
            "return_slot": ret_slot,
            "distance_km": round(dist, 1),
            "payload_kg": payload,
            "temperature_c": temp_c,
            "priority": priority,
            "source": "simulated",
        })

    trips_df = pd.DataFrame(raw_trips)

    # Feasibility repair loop
    repaired_trips = []
    for _, trip in trips_df.iterrows():
        t_dict = dict(trip)
        # Find candidate vehicles at this depot
        cand = usable_vehicles[
            (usable_vehicles["depot_id"] == t_dict["depot_id"]) &
            (usable_vehicles["payload_capacity_kg"] >= t_dict["payload_kg"])
        ]
        if cand.empty:
            # lower payload to fit at least the largest vehicle at depot
            depot_max_p = usable_vehicles[usable_vehicles["depot_id"] == t_dict["depot_id"]]["payload_capacity_kg"].max()
            t_dict["payload_kg"] = float(round(0.85 * depot_max_p, 0))
            cand = usable_vehicles[
                (usable_vehicles["depot_id"] == t_dict["depot_id"]) &
                (usable_vehicles["payload_capacity_kg"] >= t_dict["payload_kg"])
            ]

        # Check energy feasibility across candidates
        has_feasible = False
        for _, v in cand.iterrows():
            usable_kwh = v["battery_capacity_kwh"] * v["soh"]
            e_max = (v["ceiling_soc_pct"] / 100.0) * usable_kwh
            e_min = (v["reserve_soc_pct"] / 100.0) * usable_kwh
            est_energy = t_dict["distance_km"] * v["efficiency_kwh_per_km"] * (1.0 + config.trips.safety_margin)
            if est_energy + e_min <= e_max:
                has_feasible = True
                break

        if not has_feasible:
            # Shorten distance so at least one vehicle in cand can do it
            max_feasible_dist = 20.0
            for _, v in cand.iterrows():
                usable_kwh = v["battery_capacity_kwh"] * v["soh"]
                range_kwh = (v["ceiling_soc_pct"] - v["reserve_soc_pct"]) / 100.0 * usable_kwh
                veh_eff = v["efficiency_kwh_per_km"] * (1.0 + config.trips.safety_margin)
                v_max_dist = range_kwh / max(0.01, veh_eff)
                if v_max_dist > max_feasible_dist:
                    max_feasible_dist = v_max_dist

            t_dict["distance_km"] = float(round(min(t_dict["distance_km"], max(20.0, max_feasible_dist * 0.90)), 1))
            dur_slots = max(8, int(math.ceil((t_dict["distance_km"] / 35.0) * 60.0 / slot_minutes)))
            t_dict["return_slot"] = min(n_slots, t_dict["departure_slot"] + dur_slots)

        repaired_trips.append(t_dict)

    final_df = pd.DataFrame(repaired_trips)
    return final_df


def generate_all_tables(config: Config) -> dict[str, pd.DataFrame]:
    """Generates all baseline simulated tables."""
    rng = np.random.default_rng(config.random_seed)
    depots = generate_depots(config)
    chargers = generate_chargers(config, depots)
    vehicles = generate_vehicles(config, depots, rng)
    weather = generate_weather(config, rng)
    tariffs = generate_tariffs(config, depots, rng)
    trips = generate_trips(config, vehicles, weather, depots, rng)

    tables = {
        "depots": depots,
        "chargers": chargers,
        "vehicles": vehicles,
        "weather": weather,
        "tariffs": tariffs,
        "trips": trips,
    }
    validate_tables(tables, config)
    return tables


def save_tables(tables: dict[str, pd.DataFrame], out_dir: str | Path) -> None:
    """Saves tables dictionary to CSV files in out_dir."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(out_path / f"{name}.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate EV fleet operational data.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--vehicles", type=int, default=20, help="Number of vehicles")
    parser.add_argument("--out", type=str, default="data/processed", help="Output directory")
    parser.add_argument("--scenario", type=str, default="base", help="Scenario to apply")
    args = parser.parse_args()

    overrides = {
        "random_seed": args.seed,
        "fleet": {"n_vehicles": args.vehicles},
    }
    config = load_config(overrides=overrides)
    tables = generate_all_tables(config)

    if args.scenario != "base":
        from src.data.scenarios import apply_scenario
        tables = apply_scenario(args.scenario, tables, config)

    save_tables(tables, args.out)

    # Print summary
    total_v = len(tables["vehicles"])
    usable_v = len(tables["vehicles"][tables["vehicles"]["status"] == "available"])
    total_trips = len(tables["trips"])
    p_min = tables["tariffs"]["price_per_kwh"].min()
    p_max = tables["tariffs"]["price_per_kwh"].max()
    tot_dist = tables["trips"]["distance_km"].sum()

    print(f"Generated scenario '{args.scenario}' with seed {args.seed}:")
    print(f"  Vehicles: {total_v} ({usable_v} usable)")
    print(f"  Trips: {total_trips} (total {tot_dist:.1f} km)")
    print(f"  Tariff range: {p_min:.2f} - {p_max:.2f} {config.currency}/kWh")
    print(f"  Output written to {args.out}/")


if __name__ == "__main__":
    main()
