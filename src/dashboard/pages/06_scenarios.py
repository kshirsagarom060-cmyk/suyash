"""Page 6: Scenario Lab — test grid stress, outages, tariff spikes, and operational sensitivity."""

from __future__ import annotations

import streamlit as st
import pandas as pd

from src.common.config import load_config
from src.data.simulator import generate_all_tables
from src.data.scenarios import apply_scenario
from src.agents.orchestrator import run_pipeline
from src.dashboard.data_access import list_runs, load_run
from src.dashboard.components import render_kpi_card, render_alert_box, download_csv_button


def render_page() -> None:
    st.markdown("## 🧪 Scenario Simulation Lab")
    st.markdown("Stress test fleet operations against tariff spikes, charger outages, demand surges, and substation limits.")

    # 1. Interactive Scenario Controls
    with st.expander("🛠️ Configure Scenario Parameters & Constraints", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            scenario_name = st.selectbox(
                "Preset Shock Scenario",
                options=["base", "price_spike", "charger_outage", "heavy_demand", "low_battery_fleet", "site_constraint"],
                index=0,
                format_func=lambda s: {
                    "base": "Standard Operations Baseline",
                    "price_spike": "Evening Tariff Spike (2.5x from 22:00-02:00)",
                    "charger_outage": "Depot Charger Outage (2 AC + 1 DC down)",
                    "heavy_demand": "Surge Delivery Demand (+30% Trips)",
                    "low_battery_fleet": "Low Battery Fleet State (Mean SOC 20%)",
                    "site_constraint": "Restricted Substation Grid Limit (-35% Site kW)",
                }.get(s, s),
            )
            fleet_size = st.slider("Active Fleet Size (Vehicles)", min_value=4, max_value=30, value=20, step=2)

        with c2:
            site_limit_slider = st.slider("Depot Power Limit (kW)", min_value=30.0, max_value=150.0, value=90.0, step=5.0)
            time_limit = st.slider("Solver Time Budget (Seconds)", min_value=5, max_value=60, value=15, step=5)

        run_btn = st.button("🚀 Run Scenario Optimization", type="primary", use_container_width=True)

    if run_btn:
        with st.spinner(f"Simulating scenario '{scenario_name}' and optimizing schedule..."):
            try:
                overrides = {
                    "fleet": {"n_vehicles": fleet_size},
                    "site": {"limit_kw": site_limit_slider},
                    "solver": {"time_limit_s": time_limit},
                }
                cfg = load_config(overrides=overrides)
                tables = generate_all_tables(cfg)
                if scenario_name != "base":
                    tables = apply_scenario(scenario_name, tables, cfg)

                res = run_pipeline(config=cfg, tables=tables)
                st.session_state["selected_run"] = "latest"
                st.success(f"Scenario '{scenario_name}' completed successfully!")
                st.rerun()
            except Exception as e:
                st.error(f"Error running scenario: {e}")

    st.markdown("<br>", unsafe_allow_html=True)

    # 2. Historical Runs Overview
    st.markdown("### 📜 Optimization Run History")
    runs = list_runs()
    if runs:
        history_rows = []
        for r_id in runs:
            data = load_run(r_id)
            if data and data.kpis:
                sav = data.kpis.get("savings", {})
                opt = data.kpis.get("optimized", {})
                base = data.kpis.get("baseline", {})
                history_rows.append({
                    "Run ID": r_id,
                    "Timestamp": data.run_meta.get("timestamp", "N/A"),
                    "Baseline Cost (₹)": base.get("operating_cost", 0),
                    "Optimized Cost (₹)": opt.get("operating_cost", 0),
                    "Net Savings (₹)": sav.get("abs", 0),
                    "Savings (%)": sav.get("pct", 0),
                    "Trips Served": f"{opt.get('trips_served', 0)}/{opt.get('trips_total', 0)}",
                })
        
        if history_rows:
            hist_df = pd.DataFrame(history_rows)
            st.dataframe(
                hist_df,
                use_container_width=True,
                column_config={
                    "Baseline Cost (₹)": st.column_config.NumberColumn(format="₹%d"),
                    "Optimized Cost (₹)": st.column_config.NumberColumn(format="₹%d"),
                    "Net Savings (₹)": st.column_config.NumberColumn(format="₹%d"),
                    "Savings (%)": st.column_config.ProgressColumn(
                        "Savings (%)",
                        format="%.1f%%",
                        min_value=0,
                        max_value=50,
                    ),
                },
                hide_index=True,
            )


if __name__ == "__main__":
    render_page()
