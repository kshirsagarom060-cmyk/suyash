"""Page 2: Smart Charging Schedule — Gantt timelines, aggregate site load, and individual vehicle battery drill-downs."""

from __future__ import annotations

import streamlit as st
import pandas as pd

from src.dashboard.data_access import load_run
from src.dashboard.components import render_kpi_card, download_csv_button
from src.dashboard.charts import charging_gantt, price_band, site_load, vehicle_soc_trajectory


def render_page() -> None:
    st.title("⚡ Smart Charging Schedule & Grid Load")
    st.markdown("Inspect time-of-use shifted charging schedules, site power limits, and vehicle battery trajectories.")

    selected_run = st.session_state.get("selected_run", "latest")
    run_data = load_run(selected_run)

    if run_data is None or run_data.charging.empty:
        st.warning("No charging schedule data found. Please run optimization first.")
        return

    # Plan Toggle Selector
    col_t1, col_t2 = st.columns([2, 1])
    with col_t1:
        plan_choice = st.radio(
            "Select Plan Perspective",
            options=["Optimized", "Baseline", "Compare"],
            index=0,
            horizontal=True,
        )
    with col_t2:
        st.metric(
            "Peak Grid Demand",
            f"{run_data.site_load[run_data.site_load['plan'] == 'optimized']['site_kw'].max():.1f} kW" if not run_data.site_load.empty else "N/A",
            delta=f"-{run_data.site_load[run_data.site_load['plan'] == 'baseline']['site_kw'].max() - run_data.site_load[run_data.site_load['plan'] == 'optimized']['site_kw'].max():.1f} kW vs Baseline" if not run_data.site_load.empty else None,
        )

    plan_key = plan_choice.lower()
    active_plan = "optimized" if plan_key == "compare" else plan_key

    # 1. Charging Gantt Timeline
    trips_df = run_data.tables.get("trips", pd.DataFrame())
    st.plotly_chart(
        charging_gantt(run_data.charging, run_data.assignments, trips_df, plan=active_plan),
        use_container_width=True,
    )

    # 2. Tariff Price Profile
    tariffs_df = run_data.tables.get("tariffs", pd.DataFrame())
    if not tariffs_df.empty:
        st.plotly_chart(price_band(tariffs_df), use_container_width=True)

    # 3. Aggregate Site Load
    st.subheader("🔌 Aggregate Site Power vs Station Capacity")
    site_lim = float(run_data.tables.get("depots", pd.DataFrame({"site_limit_kw": [90.0]}))["site_limit_kw"].iloc[0])
    st.plotly_chart(
        site_load(run_data.site_load, plan_toggle=plan_key, site_limit_kw=site_lim),
        use_container_width=True,
    )

    # 4. Individual Vehicle Battery Drill-down
    st.subheader("🔍 Vehicle Battery State Drill-Down")
    vehicles = sorted(run_data.soc["vehicle_id"].unique()) if not run_data.soc.empty else []
    if vehicles:
        col_v1, col_v2 = st.columns([1, 3])
        with col_v1:
            sel_v = st.selectbox("Choose Vehicle", options=vehicles, index=0)
            v_info = run_data.tables.get("vehicles", pd.DataFrame())
            if not v_info.empty and sel_v in v_info.set_index("vehicle_id").index:
                row = v_info.set_index("vehicle_id").loc[sel_v]
                st.caption(f"**Model:** {row['model']}")
                st.caption(f"**Capacity:** {row['battery_capacity_kwh']} kWh")
                st.caption(f"**SOH:** {row['soh']*100:.1f}%")
                st.caption(f"**Initial SOC:** {row['current_soc_pct']}%")
                st.caption(f"**Available from:** Slot {row['available_from_slot']}")

        with col_v2:
            st.plotly_chart(
                vehicle_soc_trajectory(run_data.soc, vehicle_id=sel_v, plan=active_plan),
                use_container_width=True,
            )

    # 5. Charging Sessions Table
    st.subheader("📑 Detailed Charging Sessions")
    plan_chg_table = run_data.charging[run_data.charging["plan"] == active_plan]
    if not plan_chg_table.empty:
        st.dataframe(plan_chg_table, use_container_width=True)
        download_csv_button(plan_chg_table, f"charging_sessions_{active_plan}.csv", "📥 Export Sessions (CSV)")


if __name__ == "__main__":
    render_page()
