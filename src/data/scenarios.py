"""Scenario mutators applied on top of simulated base tables."""

from __future__ import annotations

import copy
from typing import Any, Callable, Optional
import numpy as np
import pandas as pd

from src.common.config import Config
from src.common.time_grid import get_plan_start_datetime, parse_time_window_slots


def scenario_base(tables: dict[str, pd.DataFrame], config: Config, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Base scenario: no modifications."""
    return {k: v.copy() for k, v in tables.items()}


def scenario_price_spike(tables: dict[str, pd.DataFrame], config: Config, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Price spike: multiply electricity price between 22:00 and 02:00 by 2.5x."""
    res = {k: v.copy() for k, v in tables.items()}
    tariffs = res["tariffs"].copy()
    start_dt = get_plan_start_datetime(config.horizon.plan_date, config.horizon.start_time)
    spike_slots = set(parse_time_window_slots("22:00-02:00", start_dt, config.horizon.slot_minutes, config.horizon.n_slots))
    
    mask = tariffs["slot"].isin(spike_slots)
    tariffs.loc[mask, "price_per_kwh"] = tariffs.loc[mask, "price_per_kwh"] * 2.5
    tariffs.loc[mask, "period"] = "peak"
    res["tariffs"] = tariffs
    return res


def scenario_charger_outage(tables: dict[str, pd.DataFrame], config: Config, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Charger outage: disables 2 AC chargers for 6 hours and 1 DC charger for 4 hours."""
    res = {k: v.copy() for k, v in tables.items()}
    chargers = res["chargers"]
    ac_chargers = chargers[chargers["type"] == "AC"]["charger_id"].tolist()
    dc_chargers = chargers[chargers["type"] == "DC"]["charger_id"].tolist()

    outages = []
    # Disable 2 AC chargers from slot 8 to slot 32 (6 hours)
    for c_id in ac_chargers[:2]:
        outages.append({"charger_id": c_id, "start_slot": 8, "end_slot": 32})
    # Disable 1 DC charger from slot 16 to slot 32 (4 hours)
    for c_id in dc_chargers[:1]:
        outages.append({"charger_id": c_id, "start_slot": 16, "end_slot": 32})

    res["outages"] = pd.DataFrame(outages)
    return res


def scenario_heavy_demand(tables: dict[str, pd.DataFrame], config: Config, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Heavy demand: +30% trips with more priority-1 trips."""
    res = {k: v.copy() for k, v in tables.items()}
    trips = res["trips"].copy()
    n_extra = max(1, int(round(len(trips) * 0.30)))

    extra_rows = []
    max_id = max([int(tid.split("-")[-1]) for tid in trips["trip_id"]]) if not trips.empty else 0
    
    for i in range(n_extra):
        base_trip = trips.iloc[i % len(trips)].to_dict()
        new_id = f"TRIP-{max_id + i + 1:03d}"
        base_trip["trip_id"] = new_id
        # Shift departure slot slightly
        dep = max(0, min(config.horizon.n_slots - 10, int(base_trip["departure_slot"] + rng.integers(-4, 5))))
        dur = int(base_trip["return_slot"] - base_trip["departure_slot"])
        base_trip["departure_slot"] = dep
        base_trip["return_slot"] = min(config.horizon.n_slots, dep + dur)
        base_trip["priority"] = 1 if rng.uniform() < 0.50 else 2
        extra_rows.append(base_trip)

    res["trips"] = pd.concat([trips, pd.DataFrame(extra_rows)], ignore_index=True)
    return res


def scenario_low_battery_fleet(tables: dict[str, pd.DataFrame], config: Config, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Low battery fleet: initial SOC mean reduced to 20%."""
    res = {k: v.copy() for k, v in tables.items()}
    vehicles = res["vehicles"].copy()
    new_socs = np.clip(rng.normal(20, 8, size=len(vehicles)), 5, 45)
    vehicles["current_soc_pct"] = np.round(new_socs, 1)
    res["vehicles"] = vehicles
    return res


def scenario_site_constraint(tables: dict[str, pd.DataFrame], config: Config, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Site constraint: reduces site_limit_kw by 35%."""
    res = {k: v.copy() for k, v in tables.items()}
    depots = res["depots"].copy()
    depots["site_limit_kw"] = np.round(depots["site_limit_kw"] * 0.65, 1)
    res["depots"] = depots
    return res


SCENARIOS: dict[str, Callable[[dict[str, pd.DataFrame], Config, np.random.Generator], dict[str, pd.DataFrame]]] = {
    "base": scenario_base,
    "price_spike": scenario_price_spike,
    "charger_outage": scenario_charger_outage,
    "heavy_demand": scenario_heavy_demand,
    "low_battery_fleet": scenario_low_battery_fleet,
    "site_constraint": scenario_site_constraint,
}


def apply_scenario(name: str, tables: dict[str, pd.DataFrame], config: Config, rng: Optional[np.random.Generator] = None) -> dict[str, pd.DataFrame]:
    """Applies named scenario modifier to base tables."""
    if rng is None:
        rng = np.random.default_rng(config.random_seed)
    func = SCENARIOS.get(name, scenario_base)
    return func(tables, config, rng)
