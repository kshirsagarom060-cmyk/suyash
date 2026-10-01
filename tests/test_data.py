"""Tests for data generation, schemas, scenarios, and external adapters."""

from __future__ import annotations

from unittest.mock import patch
import pandas as pd
import pytest

from src.common.config import load_config
from src.data.simulator import generate_all_tables
from src.data.scenarios import apply_scenario, SCENARIOS
from src.data.validators import validate_tables, DataValidationError
from src.data.external import fetch_open_meteo_weather, fetch_osrm_distance


def test_deterministic_generation():
    """Same seed must produce identical tables; different seeds produce differences."""
    cfg1 = load_config(overrides={"random_seed": 42})
    cfg2 = load_config(overrides={"random_seed": 42})
    cfg3 = load_config(overrides={"random_seed": 999})

    t1 = generate_all_tables(cfg1)
    t2 = generate_all_tables(cfg2)
    t3 = generate_all_tables(cfg3)

    for k in t1:
        pd.testing.assert_frame_equal(t1[k], t2[k])

    # t3 vehicles or trips should differ
    assert not t1["vehicles"]["current_soc_pct"].equals(t3["vehicles"]["current_soc_pct"])


def test_schemas_and_slot_bounds():
    """All tables adhere to required columns and valid slot intervals."""
    cfg = load_config(overrides={"random_seed": 42})
    tables = generate_all_tables(cfg)

    # Check vehicle constraints
    v = tables["vehicles"]
    assert (v["current_soc_pct"] >= 0).all() and (v["current_soc_pct"] <= 100).all()
    assert (v["reserve_soc_pct"] < v["ceiling_soc_pct"]).all()
    assert (v["battery_capacity_kwh"] > 0).all()

    # Check trips
    trips = tables["trips"]
    assert (trips["departure_slot"] < trips["return_slot"]).all()
    assert (trips["departure_slot"] >= 0).all()
    assert (trips["return_slot"] <= cfg.horizon.n_slots).all()
    assert (trips["distance_km"] > 0).all()
    assert (trips["payload_kg"] >= 0).all()


def test_tariffs_structure():
    """Tariffs cover all slots and include offpeak, shoulder, and peak periods."""
    cfg = load_config(overrides={"random_seed": 42})
    tables = generate_all_tables(cfg)
    tariffs = tables["tariffs"]

    assert len(tariffs) == cfg.horizon.n_slots
    assert set(tariffs["slot"]) == set(range(cfg.horizon.n_slots))
    periods = set(tariffs["period"])
    assert "offpeak" in periods
    assert "shoulder" in periods
    assert "peak" in periods
    assert (tariffs["price_per_kwh"] > 0).all()


def test_trip_feasibility_base_scenario():
    """Every trip in the base scenario must have at least one eligible vehicle."""
    cfg = load_config(overrides={"random_seed": 42})
    tables = generate_all_tables(cfg)
    # validate_tables should raise no exceptions and return empty or minor warnings
    warnings = validate_tables(tables, cfg, allow_impossible_trips=False)
    assert len(warnings) == 0


def test_all_scenarios_execute():
    """Each scenario runs and produces valid tables."""
    cfg = load_config(overrides={"random_seed": 42})
    base_tables = generate_all_tables(cfg)

    for sc_name in SCENARIOS:
        sc_tables = apply_scenario(sc_name, base_tables, cfg)
        assert "vehicles" in sc_tables
        assert "trips" in sc_tables
        # Stress scenarios may emit warnings about impossible trips
        warnings = validate_tables(sc_tables, cfg, allow_impossible_trips=True)
        assert isinstance(warnings, list)


def test_external_fallback_on_network_failure():
    """External adapters return None / graceful fallback when network fails."""
    cfg = load_config(overrides={"random_seed": 42})
    with patch("requests.get", side_effect=Exception("Connection refused")):
        res_weather = fetch_open_meteo_weather(28.61, 77.20, cfg)
        assert res_weather is None or isinstance(res_weather, pd.DataFrame)
        
        res_osrm = fetch_osrm_distance(28.61, 77.20, 28.70, 77.30)
        assert res_osrm is None or isinstance(res_osrm, float)
