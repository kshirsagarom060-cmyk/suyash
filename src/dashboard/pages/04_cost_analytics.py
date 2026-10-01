"""Page 4: Cost & Optimization Analytics — financial savings, tariff shifts, and solver diagnostics."""

from __future__ import annotations

import streamlit as st
import pandas as pd

from src.dashboard.data_access import load_run
from src.dashboard.components import render_kpi_card, render_alert_box, download_csv_button
from src.dashboard.charts import cost_breakdown, energy_by_period, charger_utilization


def render_page() -> None:
    st.title("💰 Cost & Optimization Analytics")
    st.markdown("Quantify financial electricity savings, peak demand shaving, and solver performance metrics.")

    selected_run = st.session_state.get("selected_run", "latest")
    run_data = load_run(selected_run)

    if run_data is None or not run_data.kpis:
        st.warning("No KPI data found. Please run the optimization pipeline.")
        return

    kpis = run_data.kpis
    b = kpis.get("baseline", {})
    o = kpis.get("optimized", {})
    sav = kpis.get("savings", {})
    solver = kpis.get("solver", {})

    curr = run_data.run_meta.get("config", {}).get("currency", "INR")

    # 1. Headline Financial Cards
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_kpi_card(
            f"Baseline Cost ({curr})",
            f"{b.get('operating_cost', 0):,.2f}",
            "Unmanaged plug-and-charge policy",
            delta_color="#9CA3AF"
        )
    with c2:
        render_kpi_card(
            f"Optimized Cost ({curr})",
            f"{o.get('operating_cost', 0):,.2f}",
            "Time-of-use smart schedule",
            delta_color="#3B82F6"
        )
    with c3:
        render_kpi_card(
            f"Net Savings ({curr})",
            f"{sav.get('abs', 0):,.2f}",
            f"{sav.get('pct', 0):.1f}% Total Operating Savings",
            delta_color="#10B981"
        )
    with c4:
        render_kpi_card(
            "Off-Peak Load Shift",
            f"{sav.get('kwh_shifted_out_of_peak', 0):.1f} kWh",
            "Shifted from expensive peak hours",
            delta_color="#10B981"
        )

    st.caption("ℹ️ *Savings = Baseline Operating Cost − Optimized Operating Cost (evaluated with identical tariffs and vehicle data).*")
    st.markdown("---")

    # 2. Charts Row
    ch1, ch2 = st.columns(2)
    with ch1:
        st.plotly_chart(cost_breakdown(kpis), use_container_width=True)
    with ch2:
        st.plotly_chart(energy_by_period(kpis), use_container_width=True)

    # 3. Charger Utilization
    st.subheader("🔌 Charger Utilization")
    st.plotly_chart(charger_utilization(kpis), use_container_width=True)

    # 4. Solver Performance Panel
    st.subheader("⚙️ Solver Engine & Execution Diagnostics")
    sc1, sc2, sc3, sc4 = st.columns(4)
    with sc1:
        st.metric("Solver Algorithm", solver.get("method", "CBC MILP"))
    with sc2:
        st.metric("Solve Status", solver.get("status", "Optimal"))
    with sc3:
        st.metric("Solver Runtime", f"{solver.get('runtime_s', 0):.2f}s")
    with sc4:
        st.metric("Problem Scale", f"{solver.get('n_variables', 0):,} vars | {solver.get('n_constraints', 0):,} cons")


if __name__ == "__main__":
    render_page()
