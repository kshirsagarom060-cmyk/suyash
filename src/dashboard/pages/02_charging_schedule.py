"""Page 2: Smart Charging Schedule — Gantt timelines, aggregate site load, and individual vehicle battery drill-downs."""

from __future__ import annotations

import streamlit as st
import pandas as pd

from src.dashboard.data_access import load_run
from src.dashboard.components import render_kpi_card, download_csv_button
from src.dashboard.charts import charging_gantt, price_band, site_load, vehicle_soc_trajectory


def render_page() -> None:
    st.markdown("## ⚡ Smart Charging Schedule & Grid Load")
    st.markdown("Inspect time-of-use shifted charging schedules, site power limits, and vehicle battery trajectories.")

    selected_run = st.session_state.get("selected_run", "latest")
    run_data = load_run(selected_run)

    if run_data is None or run_data.charging.empty:
        st.warning("No charging schedule data found. Please run optimization first.")
        return

    # Top Perspective & Peak Power Metrics
    col_t1, col_t2 = st.columns([2, 1])
    with col_t1:
        plan_choice = st.radio(
            "Schedule Perspective",
            options=["Optimized", "Baseline", "Compare"],
            index=0,
            horizontal=True,
        )
    with col_t2:
        if not run_data.site_load.empty:
            opt_peak = run_data.site_load[run_data.site_load['plan'] == 'optimized']['site_kw'].max()
            base_peak = run_data.site_load[run_data.site_load['plan'] == 'baseline']['site_kw'].max()
            shaved = base_peak - opt_peak
            st.metric(
                "Peak Grid Demand",
                f"{opt_peak:.1f} kW",
                delta=f"-{shaved:.1f} kW peak shaved" if shaved > 0 else "0 kW shaved",
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
    st.markdown("### 🔌 Aggregate Site Power vs Station Capacity")
    site_lim = float(run_data.tables.get("depots", pd.DataFrame({"site_limit_kw": [90.0]}))["site_limit_kw"].iloc[0])
    st.plotly_chart(
        site_load(run_data.site_load, plan_toggle=plan_key, site_limit_kw=site_lim),
        use_container_width=True,
    )

    # 4. Individual Vehicle Battery Drill-down
    st.markdown("### 🔍 Individual Vehicle Battery Trajectory")
    vehicles = sorted(run_data.soc["vehicle_id"].unique()) if not run_data.soc.empty else []
    if vehicles:
        col_v1, col_v2 = st.columns([1, 3])
        with col_v1:
            sel_v = st.selectbox("Select Vehicle", options=vehicles, index=0)
            v_info = run_data.tables.get("vehicles", pd.DataFrame())
            if not v_info.empty and sel_v in v_info.set_index("vehicle_id").index:
                row = v_info.set_index("vehicle_id").loc[sel_v]
                st.markdown(
                    f"""
                    <div style="background: rgba(30,41,59,0.5); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 14px; font-size: 0.85rem;">
                        <div style="color: #38BDF8; font-weight: 700; font-size: 1rem; margin-bottom: 6px;">{sel_v} Specs</div>
                        <div>• <b>Model:</b> {row['model']}</div>
                        <div>• <b>Capacity:</b> {row['battery_capacity_kwh']} kWh</div>
                        <div>• <b>SOH:</b> {row['soh']*100:.1f}%</div>
                        <div>• <b>Initial SOC:</b> {row['current_soc_pct']}%</div>
                        <div>• <b>Depot Arrival:</b> Slot {row['available_from_slot']}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        with col_v2:
            st.plotly_chart(
                vehicle_soc_trajectory(run_data.soc, vehicle_id=sel_v, plan=active_plan),
                use_container_width=True,
            )

    # 5. Charging Sessions Table
    st.markdown("### 📑 Detailed Charging Sessions")
    plan_chg_table = run_data.charging[run_data.charging["plan"] == active_plan]
    if not plan_chg_table.empty:
        st.dataframe(
            plan_chg_table,
            use_container_width=True,
            column_config={
                "power_kw_grid": st.column_config.NumberColumn("Grid Power (kW)", format="%.1f kW"),
                "energy_to_battery_kwh": st.column_config.NumberColumn("Battery Energy (kWh)", format="%.2f kWh"),
                "price_per_kwh": st.column_config.NumberColumn("Tariff (₹/kWh)", format="₹%.2f"),
            },
            hide_index=True,
        )
        download_csv_button(plan_chg_table, f"charging_sessions_{active_plan}.csv", "📥 Export Sessions (CSV)")


if __name__ == "__main__":
    render_page()
