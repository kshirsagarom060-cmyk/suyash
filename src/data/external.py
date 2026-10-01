"""External data adapters with offline caching, retry logic, and simulated fallback."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional
import pandas as pd
import requests
from dotenv import load_dotenv

from src.common.config import Config
from src.common.time_grid import get_plan_start_datetime, slot_to_datetime

load_dotenv()
logger = logging.getLogger("ev_fleet_optimizer.external")

CACHE_DIR = Path("data/external_cache")


def _get_cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    hashed = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{hashed}.json"


def _read_cache(key: str) -> Optional[dict[str, Any]]:
    path = _get_cache_path(key)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _write_cache(key: str, data: dict[str, Any]) -> None:
    path = _get_cache_path(key)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Failed to write cache for {key}: {e}")


def fetch_open_meteo_weather(lat: float, lon: float, config: Config) -> Optional[pd.DataFrame]:
    """Fetches real weather from Open-Meteo API, caches response, and interpolates to slots."""
    start_dt = get_plan_start_datetime(config.horizon.plan_date, config.horizon.start_time)
    end_dt = slot_to_datetime(config.horizon.n_slots - 1, start_dt, config.horizon.slot_minutes)
    
    cache_key = f"open_meteo_{lat:.4f}_{lon:.4f}_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}"
    cached_data = _read_cache(cache_key)
    
    data = None
    if cached_data is not None:
        data = cached_data
    else:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m",
            "start_date": start_dt.strftime("%Y-%m-%d"),
            "end_date": (end_dt + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    _write_cache(cache_key, data)
                    break
            except Exception as e:
                logger.warning(f"Open-Meteo attempt {attempt+1} failed: {e}")
                
    if not data or "hourly" not in data or "time" not in data["hourly"]:
        return None

    times = pd.to_datetime(data["hourly"]["time"])
    temps = data["hourly"]["temperature_2m"]
    hourly_df = pd.DataFrame({"datetime": times, "temperature_c": temps}).set_index("datetime")
    
    rows = []
    for s in range(config.horizon.n_slots):
        slot_dt = slot_to_datetime(s, start_dt, config.horizon.slot_minutes)
        # Interpolate nearest hourly temperature
        nearest_idx = hourly_df.index.get_indexer([slot_dt], method="nearest")[0]
        temp_val = float(hourly_df.iloc[nearest_idx]["temperature_c"])
        rows.append({
            "slot": s,
            "timestamp": slot_dt.strftime("%Y-%m-%d %H:%M"),
            "temperature_c": temp_val,
            "source": "real:open-meteo",
        })
    return pd.DataFrame(rows)


def fetch_osrm_distance(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float) -> Optional[float]:
    """Fetches real route distance in km from OSRM demo server."""
    cache_key = f"osrm_{origin_lat:.4f}_{origin_lon:.4f}_{dest_lat:.4f}_{dest_lon:.4f}"
    cached = _read_cache(cache_key)
    if cached is not None and "distance_km" in cached:
        return float(cached["distance_km"])

    url = f"http://router.project-osrm.org/route/v1/driving/{origin_lon},{origin_lat};{dest_lon},{dest_lat}?overview=false"
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if "routes" in data and len(data["routes"]) > 0:
                    dist_km = data["routes"][0]["distance"] / 1000.0
                    _write_cache(cache_key, {"distance_km": dist_km})
                    return float(dist_km)
        except Exception as e:
            logger.warning(f"OSRM route attempt {attempt+1} failed: {e}")
    return None


def load_tariff_csv(path: str | Path, config: Config) -> pd.DataFrame:
    """Loads a user-supplied tariff CSV (timestamp, price_per_kwh) and resamples to slot grid."""
    df = pd.read_csv(path)
    if "timestamp" not in df.columns or "price_per_kwh" not in df.columns:
        raise ValueError("Tariff CSV must contain 'timestamp' and 'price_per_kwh' columns.")
    
    df["datetime"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("datetime").set_index("datetime")
    
    start_dt = get_plan_start_datetime(config.horizon.plan_date, config.horizon.start_time)
    rows = []
    for s in range(config.horizon.n_slots):
        slot_dt = slot_to_datetime(s, start_dt, config.horizon.slot_minutes)
        nearest_idx = df.index.get_indexer([slot_dt], method="nearest")[0]
        price = float(df.iloc[nearest_idx]["price_per_kwh"])
        # Determine period label
        hour = slot_dt.hour
        if 22 <= hour or hour < 6:
            period = "offpeak"
        elif 6 <= hour < 18:
            period = "shoulder"
        else:
            period = "peak"
        rows.append({
            "slot": s,
            "timestamp": slot_dt.strftime("%Y-%m-%d %H:%M"),
            "depot_id": "DEPOT-01",
            "price_per_kwh": price,
            "period": period,
            "source": "imported:csv",
        })
    return pd.DataFrame(rows)


def import_vehicles_csv(path: str | Path, col_mapping: Optional[dict[str, str]] = None) -> pd.DataFrame:
    """Imports user fleet vehicles CSV with column mapping."""
    df = pd.read_csv(path)
    if col_mapping:
        df = df.rename(columns=col_mapping)
    
    required = ["vehicle_id", "model", "depot_id", "battery_capacity_kwh", "soh", "efficiency_kwh_per_km", "payload_capacity_kg", "max_ac_kw", "max_dc_kw", "current_soc_pct", "available_from_slot", "status", "reserve_soc_pct", "ceiling_soc_pct"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Imported vehicles CSV missing required columns: {missing}")
    
    if "source" not in df.columns:
        df["source"] = "imported:csv"
    return df
