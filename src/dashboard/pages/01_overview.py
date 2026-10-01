"""Page 1: Fleet Overview — inventory, initial state of charge, trip demand, and supply balance."""

from __future__ import annotations

import streamlit as st
import pandas as pd

from src.dashboard.data_access import load_run
from src.dashboard.components import render_kpi_card, render_alert_box, download_csv_button, render_data_source_badge
from src.dashboard.charts import soc_bar, demand_vs_supply


def render_page() -> None:
    st.title("🚗 Fleet Overview & Initial Status")
    st.markdown("Monitor fleet readiness, initial battery states, and daily demand distribution.")

    selected_run = st.session_state.get("selected_run", "latest")
    run_data = load_run(selected_run)

    if run_data is None:
        st.warning("No optimization run results found. Please execute the pipeline from the sidebar.")
        return

    vehicles_df = run_data.tables.get("vehicles", pd.DataFrame())
    trips_df = run_data.tables.get("trips", pd.DataFrame())
    kpis = run_data.kpis

    if vehicles_df.empty:
        st.info("No vehicles data found.")
        return

    # Data Source Header Badge
    source_val = vehicles_df["source"].iloc[0] if "source" in vehicles_df.columns else "simulated"
    col_hdr1, col_hdr2 = st.columns([3, 1])
    with col_hdr1:
        st.caption(f"Showing results for run: `{run_data.run_id}`")
    with col_hdr2:
        render_data_source_badge(source_val)

    # 1. Headline KPIs
    n_total = len(vehicles_df)
    n_usable = len(vehicles_df[vehicles_df["status"] == "available"])
    n_maint = len(vehicles_df[vehicles_df["status"] == "maintenance"])
    n_trips = len(trips_df)

    p1_trips = len(trips_df[trips_df["priority"] == 1]) if not trips_df.empty else 0
    p2_trips = len(trips_df[trips_df["priority"] == 2]) if not trips_df.empty else 0
    p3_trips = len(trips_df[trips_df["priority"] == 3]) if not trips_df.empty else 0

    low_soc_count = len(vehicles_df[vehicles_df["current_soc_pct"] < vehicles_df["reserve_soc_pct"]])

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_kpi_card("Fleet Size", f"{n_total} EVs", f"{n_usable} Available | {n_maint} Maint")
    with c2:
        render_kpi_card("Scheduled Trips", f"{n_trips} Trips", f"P1: {p1_trips} | P2: {p2_trips} | P3: {p3_trips}")
    with c3:
        render_kpi_card("Battery Health", f"{vehicles_df['soh'].mean()*100:.1f}% Avg SOH", f"Min SOH: {vehicles_df['soh'].min()*100:.1f}%")
    with c4:
        delta_col = "#EF4444" if low_soc_count > 0 else "#10B981"
        render_kpi_card("Low-SOC Warnings", f"{low_soc_count} Vehicles", "Below configured reserve", delta_color=delta_col)

    st.markdown("---")

    # 2. Charts Row
    ch_col1, ch_col2 = st.columns(2)
    with ch_col1:
        res_limit = float(vehicles_df["reserve_soc_pct"].iloc[0]) if not vehicles_df.empty else 15.0
        ceil_limit = float(vehicles_df["ceiling_soc_pct"].iloc[0]) if not vehicles_df.empty else 90.0
        st.plotly_chart(soc_bar(vehicles_df, res_limit, ceil_limit), use_container_width=True)

    with ch_col2:
        # Compute hourly demand from trips table
        if not trips_df.empty:
            hour_bins = {}
            for _, tr in trips_df.iterrows():
                dep_hr = int(tr["departure_slot"]) // 4  # Approximate hour
                if dep_hr not in hour_bins:
                    hour_bins[dep_hr] = {"hour": dep_hr, "p1": 0, "p2": 0, "p3": 0}
                pri = int(tr["priority"])
                if pri == 1:
                    hour_bins[dep_hr]["p1"] += 1
                elif pri == 2:
                    hour_bins[dep_hr]["p2"] += 1
                else:
                    hour_bins[dep_hr]["p3"] += 1
            demand_df = pd.DataFrame(list(hour_bins.values())).sort_values("hour")
        else:
            demand_df = pd.DataFrame()

        st.plotly_chart(demand_vs_supply(demand_df, n_usable), use_container_width=True)

    # 3. Fleet Inventory Table
    st.subheader("📋 Fleet Vehicle Roster & State")
    display_cols = [
        "vehicle_id", "model", "depot_id", "battery_capacity_kwh", "soh",
        "current_soc_pct", "reserve_soc_pct", "ceiling_soc_pct",
        "max_ac_kw", "max_dc_kw", "available_from_slot", "status"
    ]
    avail_cols = [c for c in display_cols if c in vehicles_df.columns]
    st.dataframe(vehicles_df[avail_cols], use_container_width=True)
    download_csv_button(vehicles_df[avail_cols], "fleet_vehicles.csv", "📥 Export Fleet Roster (CSV)")


if __name__ == "__main__":
    render_page()
