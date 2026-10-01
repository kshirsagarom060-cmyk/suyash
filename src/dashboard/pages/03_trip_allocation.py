"""Page 3: Fleet Trip Allocation — vehicle assignments, trip margins, feasibility buffers, and change explanations."""

from __future__ import annotations

import streamlit as st
import pandas as pd

from src.dashboard.data_access import load_run
from src.dashboard.components import render_kpi_card, render_alert_box, download_csv_button
from src.dashboard.charts import trip_timeline, margin_hist


def render_page() -> None:
    st.markdown("## 🎯 Trip Allocation & Dispatch Dispatcher")
    st.markdown("Track vehicle-to-trip assignments, departure battery safety margins, and operational reallocations.")

    selected_run = st.session_state.get("selected_run", "latest")
    run_data = load_run(selected_run)

    if run_data is None or run_data.assignments.empty:
        st.warning("No trip assignment data found. Please run optimization first.")
        return

    trips_df = run_data.tables.get("trips", pd.DataFrame())
    opt_ass = run_data.assignments[run_data.assignments["plan"] == "optimized"]
    base_ass = run_data.assignments[run_data.assignments["plan"] == "baseline"]

    # 1. Summary Metrics Cards with icons
    n_served = int(opt_ass["served"].sum()) if not opt_ass.empty else 0
    n_total = len(opt_ass)
    n_unserved = n_total - n_served
    n_veh_used = len(opt_ass[opt_ass["served"] == True]["vehicle_id"].unique())

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        fulfillment = (n_served / max(1, n_total)) * 100.0
        render_kpi_card("Trips Served", f"{n_served} / {n_total}", f"{fulfillment:.1f}% fulfillment", delta_color="#10B981", icon="🎯")
    with c2:
        delta_col = "#EF4444" if n_unserved > 0 else "#10B981"
        sub = "Zero missed trips" if n_unserved == 0 else f"{n_unserved} dropped trips"
        render_kpi_card("Unserved Trips", f"{n_unserved}", sub, delta_color=delta_col, icon="⚠️")
    with c3:
        render_kpi_card("Vehicles Dispatched", f"{n_veh_used} EVs", "Active across horizon", delta_color="#38BDF8", icon="🚚")
    with c4:
        avg_dep_soc = opt_ass[opt_ass["served"] == True]["departure_soc_pct"].mean() if not opt_ass.empty else 0.0
        render_kpi_card("Avg Departure SOC", f"{avg_dep_soc:.1f}%", "Battery state at dispatch", delta_color="#10B981", icon="🔋")

    st.markdown("<br>", unsafe_allow_html=True)

    # 2. Charts Row
    ch1, ch2 = st.columns(2)
    with ch1:
        st.plotly_chart(trip_timeline(run_data.assignments, trips_df, plan="optimized"), use_container_width=True)
    with ch2:
        st.plotly_chart(margin_hist(run_data.assignments, trips_df, run_data.soc, None), use_container_width=True)

    # 3. Comprehensive Trip Allocation Table
    st.markdown("### 📋 Dispatch Allocation Roster")
    merged = pd.DataFrame()
    if not opt_ass.empty and not base_ass.empty:
        merged = pd.merge(
            trips_df,
            opt_ass[["trip_id", "vehicle_id", "served", "departure_soc_pct"]].rename(
                columns={"vehicle_id": "opt_vehicle", "served": "opt_served", "departure_soc_pct": "opt_dep_soc"}
            ),
            on="trip_id",
            how="left",
        )
        merged = pd.merge(
            merged,
            base_ass[["trip_id", "vehicle_id", "served", "departure_soc_pct"]].rename(
                columns={"vehicle_id": "base_vehicle", "served": "base_served", "departure_soc_pct": "base_dep_soc"}
            ),
            on="trip_id",
            how="left",
        )

    if not merged.empty:
        st.dataframe(
            merged,
            use_container_width=True,
            column_config={
                "opt_dep_soc": st.column_config.ProgressColumn(
                    "Optimized Departure SOC (%)",
                    format="%.1f%%",
                    min_value=0,
                    max_value=100,
                ),
                "distance_km": st.column_config.NumberColumn("Distance (km)", format="%.1f km"),
                "required_energy_kwh": st.column_config.NumberColumn("Required (kWh)", format="%.1f kWh"),
                "priority": st.column_config.NumberColumn("Priority", format="P%d"),
            },
            hide_index=True,
        )
        download_csv_button(merged, "trip_allocation_comparison.csv", "📥 Export Allocation Table (CSV)")

    # 4. What Changed Section
    st.markdown("### 🔄 What Changed: Baseline vs Optimized Assignments")
    if not merged.empty:
        changed_trips = merged[merged["opt_vehicle"] != merged["base_vehicle"]]
        if not changed_trips.empty:
            st.info(f"💡 Found **{len(changed_trips)} trips** with strategically reallocated vehicles to minimize battery wear and maximize cheap off-peak charging.")
            for _, r in changed_trips.iterrows():
                b_v = r["base_vehicle"] if pd.notna(r["base_vehicle"]) and r["base_vehicle"] else "Unserved"
                o_v = r["opt_vehicle"] if pd.notna(r["opt_vehicle"]) and r["opt_vehicle"] else "Unserved"
                st.markdown(
                    f"- **{r['trip_id']}** (Priority {r['priority']}, {r['distance_km']} km): "
                    f"Reassigned from `{b_v}` → `{o_v}` (Departure SOC: **{r.get('opt_dep_soc', 0):.1f}%**)."
                )
        else:
            st.success("✅ All served trips share optimal vehicle assignments between baseline and optimized solutions.")


if __name__ == "__main__":
    render_page()
