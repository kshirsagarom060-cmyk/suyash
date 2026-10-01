"""CSV loading and dtype enforcement for all input tables."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
import pandas as pd


DTYPES = {
    "depots": {
        "depot_id": str,
        "name": str,
        "site_limit_kw": float,
        "lat": float,
        "lon": float,
        "source": str,
    },
    "chargers": {
        "charger_id": str,
        "depot_id": str,
        "type": str,
        "max_kw": float,
        "efficiency": float,
        "derate_factor": float,
        "source": str,
    },
    "vehicles": {
        "vehicle_id": str,
        "model": str,
        "depot_id": str,
        "battery_capacity_kwh": float,
        "soh": float,
        "efficiency_kwh_per_km": float,
        "payload_capacity_kg": float,
        "max_ac_kw": float,
        "max_dc_kw": float,
        "current_soc_pct": float,
        "available_from_slot": int,
        "status": str,
        "reserve_soc_pct": float,
        "ceiling_soc_pct": float,
        "source": str,
    },
    "trips": {
        "trip_id": str,
        "depot_id": str,
        "departure_slot": int,
        "return_slot": int,
        "distance_km": float,
        "payload_kg": float,
        "temperature_c": float,
        "priority": int,
        "source": str,
    },
    "tariffs": {
        "slot": int,
        "timestamp": str,
        "depot_id": str,
        "price_per_kwh": float,
        "period": str,
        "source": str,
    },
    "weather": {
        "slot": int,
        "timestamp": str,
        "temperature_c": float,
        "source": str,
    },
    "outages": {
        "charger_id": str,
        "start_slot": int,
        "end_slot": int,
    },
}


def load_tables(data_dir: str | Path) -> dict[str, pd.DataFrame]:
    """Loads all CSV tables from data_dir with dtype casting."""
    data_dir = Path(data_dir)
    tables: dict[str, pd.DataFrame] = {}

    expected_tables = ["depots", "chargers", "vehicles", "trips", "tariffs", "weather"]
    optional_tables = ["outages"]

    for name in expected_tables + optional_tables:
        csv_path = data_dir / f"{name}.csv"
        if csv_path.exists():
            df = pd.read_csv(csv_path)
            dtype_map = DTYPES.get(name, {})
            for col, dtype in dtype_map.items():
                if col in df.columns:
                    try:
                        df[col] = df[col].astype(dtype)
                    except Exception:
                        pass
            tables[name] = df
        elif name in expected_tables:
            raise FileNotFoundError(f"Required table {name}.csv missing from {data_dir}")

    return tables
