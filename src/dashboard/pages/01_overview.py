"""Page 1: Fleet Overview — inventory, initial state of charge, trip demand, and supply balance."""

from __future__ import annotations

import streamlit as st
import pandas as pd

from src.dashboard.data_access import load_run
from src.dashboard.components import render_kpi_card, render_alert_box, download_csv_button, render_data_source_badge
from src.dashboard.charts import soc_bar, demand_vs_supply


def render_page() -> None:
    st.markdown("## 🚗 Fleet Inventory & Battery Telemetry")
    st.markdown("Monitor fleet readiness, initial battery state of charge, and daily trip demand distribution.")

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

    # 1. Headline KPIs with modern icons
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
        render_kpi_card("Fleet Capacity", f"{n_total} EVs", f"{n_usable} Active | {n_maint} Maintenance", delta_color="#38BDF8", icon="🚗")
    with c2:
        render_kpi_card("Scheduled Trips", f"{n_trips} Trips", f"Critical: {p1_trips} | Standard: {p2_trips + p3_trips}", delta_color="#38BDF8", icon="🎯")
    with c3:
        render_kpi_card("Battery SOH", f"{vehicles_df['soh'].mean()*100:.1f}%", f"Min SOH: {vehicles_df['soh'].min()*100:.1f}%", delta_color="#10B981", icon="🔋")
    with c4:
        delta_col = "#EF4444" if low_soc_count > 0 else "#10B981"
        sub_text = "Action required" if low_soc_count > 0 else "All above reserve"
        render_kpi_card("Low-SOC Alert", f"{low_soc_count} Vehicles", sub_text, delta_color=delta_col, icon="⚠️")

    st.markdown("<br>", unsafe_allow_html=True)

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
    st.markdown("### 📋 Fleet Vehicle Roster & Telemetry")
    display_cols = [
        "vehicle_id", "model", "depot_id", "battery_capacity_kwh", "soh",
        "current_soc_pct", "reserve_soc_pct", "ceiling_soc_pct",
        "max_ac_kw", "max_dc_kw", "available_from_slot", "status"
    ]
    avail_cols = [c for c in display_cols if c in vehicles_df.columns]
    
    # Styled dataframe
    st.dataframe(
        vehicles_df[avail_cols],
        use_container_width=True,
        column_config={
            "current_soc_pct": st.column_config.ProgressColumn(
                "Current SOC (%)",
                help="Current battery state of charge",
                format="%.1f%%",
                min_value=0,
                max_value=100,
            ),
            "soh": st.column_config.NumberColumn(
                "State of Health",
                format="%.2f",
            ),
            "battery_capacity_kwh": st.column_config.NumberColumn(
                "Capacity (kWh)",
                format="%.0f kWh",
            ),
            "max_ac_kw": st.column_config.NumberColumn("AC Max (kW)", format="%.1f kW"),
            "max_dc_kw": st.column_config.NumberColumn("DC Max (kW)", format="%.1f kW"),
        },
        hide_index=True,
    )
    download_csv_button(vehicles_df[avail_cols], "fleet_vehicles.csv", "📥 Export Fleet Roster (CSV)")


if __name__ == "__main__":
    render_page()
