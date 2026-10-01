"""Unit and integration tests for dashboard data loading, chart builders, and Streamlit execution."""

from __future__ import annotations

from pathlib import Path
import pandas as pd
import pytest
import plotly.graph_objects as go
from streamlit.testing.v1 import AppTest

from src.dashboard.data_access import list_runs, load_run
import src.dashboard.charts as charts


@pytest.fixture
def sample_kpis():
    return {
        "baseline": {
            "operating_cost": 21000.0,
            "energy_cost": 7000.0,
            "demand_cost": 13500.0,
            "wear_cost": 500.0,
            "energy_by_period_kwh": {"offpeak": 100.0, "shoulder": 200.0, "peak": 300.0},
        },
        "optimized": {
            "operating_cost": 18000.0,
            "energy_cost": 5500.0,
            "demand_cost": 12000.0,
            "wear_cost": 500.0,
            "energy_by_period_kwh": {"offpeak": 300.0, "shoulder": 200.0, "peak": 50.0},
            "charger_utilization": {"AC": 0.45, "DC": 0.60},
        },
        "savings": {
            "abs": 3000.0,
            "pct": 14.3,
            "kwh_shifted_out_of_peak": 250.0,
        },
    }


def test_chart_builders_normal_and_empty(sample_kpis):
    """Every chart builder must return a valid plotly Figure for both populated and empty inputs."""
    # 1. empty_fig
    assert isinstance(charts.empty_fig(), go.Figure)

    # 2. soc_bar
    df_soc = pd.DataFrame({"vehicle_id": ["EV-01", "EV-02"], "current_soc_pct": [30.0, 80.0]})
    assert isinstance(charts.soc_bar(df_soc), go.Figure)
    assert isinstance(charts.soc_bar(pd.DataFrame()), go.Figure)

    # 3. demand_vs_supply
    df_dem = pd.DataFrame({"hour": [8, 9], "p1": [1, 2], "p2": [2, 1], "p3": [0, 1]})
    assert isinstance(charts.demand_vs_supply(df_dem, 10), go.Figure)
    assert isinstance(charts.demand_vs_supply(pd.DataFrame(), 10), go.Figure)

    # 4. price_band
    df_tar = pd.DataFrame({"slot": [0, 1, 2], "price_per_kwh": [4.5, 7.0, 10.5]})
    assert isinstance(charts.price_band(df_tar), go.Figure)
    assert isinstance(charts.price_band(pd.DataFrame()), go.Figure)

    # 5. site_load
    df_load = pd.DataFrame({"slot": [0, 1], "site_kw": [45.0, 60.0], "plan": ["optimized", "optimized"]})
    assert isinstance(charts.site_load(df_load), go.Figure)
    assert isinstance(charts.site_load(pd.DataFrame()), go.Figure)

    # 6. vehicle_soc_trajectory
    df_v_soc = pd.DataFrame({"vehicle_id": ["EV-01", "EV-01"], "slot": [0, 1], "energy_kwh": [20.0, 25.0], "plan": ["optimized", "optimized"]})
    assert isinstance(charts.vehicle_soc_trajectory(df_v_soc, "EV-01"), go.Figure)
    assert isinstance(charts.vehicle_soc_trajectory(pd.DataFrame(), "EV-01"), go.Figure)

    # 7. cost_breakdown
    assert isinstance(charts.cost_breakdown(sample_kpis), go.Figure)
    assert isinstance(charts.cost_breakdown({}), go.Figure)

    # 8. energy_by_period
    assert isinstance(charts.energy_by_period(sample_kpis), go.Figure)
    assert isinstance(charts.energy_by_period({}), go.Figure)

    # 9. charger_utilization
    assert isinstance(charts.charger_utilization(sample_kpis), go.Figure)
    assert isinstance(charts.charger_utilization({}), go.Figure)

    # 10. margin_hist
    df_ass = pd.DataFrame({"trip_id": ["TRIP-01"], "vehicle_id": ["EV-01"], "served": [True]})
    df_trips = pd.DataFrame({"trip_id": ["TRIP-01"], "departure_slot": [10]})
    df_soc_m = pd.DataFrame({"vehicle_id": ["EV-01"], "slot": [10], "energy_kwh": [30.0]})
    df_elig = pd.DataFrame({"trip_id": ["TRIP-01"], "vehicle_id": ["EV-01"], "required_departure_kwh": [25.0]})
    assert isinstance(charts.margin_hist(df_ass, df_trips, df_soc_m, df_elig), go.Figure)
    assert isinstance(charts.margin_hist(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()), go.Figure)

    # 11. tradeoff_curve
    t_data = [{"parameter_value": 1, "total_cost": 100}, {"parameter_value": 2, "total_cost": 90}]
    assert isinstance(charts.tradeoff_curve(t_data), go.Figure)
    assert isinstance(charts.tradeoff_curve([]), go.Figure)


def test_data_access_loading_and_fallbacks():
    """data_access loads latest run and gracefully handles missing runs."""
    runs = list_runs()
    assert isinstance(runs, list)

    latest_data = load_run("latest")
    if latest_data is not None:
        assert hasattr(latest_data, "kpis")
        assert hasattr(latest_data, "charging")
        assert hasattr(latest_data, "assignments")

    # Missing run returns None
    missing_data = load_run("non_existent_run_dir_12345")
    assert missing_data is None


def test_streamlit_app_renders():
    """Streamlit AppTest confirms app.py initializes and renders without exception."""
    app_path = (Path(__file__).resolve().parent.parent / "src" / "dashboard" / "app.py")
    at = AppTest.from_file(str(app_path))
    at.run(timeout=15)
    assert not at.exception
