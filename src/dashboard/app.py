"""Streamlit Dashboard Entrypoint for AI Energy & EV Fleet Optimization Agent."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import pandas as pd

from src.common.config import load_config
from src.dashboard.data_access import list_runs, load_run
from src.dashboard.components import render_custom_css, render_data_source_badge
from src.agents.orchestrator import run_pipeline

# Page Configuration
st.set_page_config(
    page_title="AI Energy & EV Fleet Optimizer",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

render_custom_css()


def main() -> None:
    # Sidebar Navigation & Execution Controls
    with st.sidebar:
        st.title("⚡ EV Fleet Optimizer")
        st.caption("AI Decision-Support System")
        st.markdown("---")

        # Available Runs Selector
        available_runs = list_runs()
        if "selected_run" not in st.session_state:
            st.session_state["selected_run"] = available_runs[0] if available_runs else "latest"

        sel_run = st.selectbox(
            "Select Run Output",
            options=available_runs if available_runs else ["latest"],
            index=0 if available_runs else 0,
        )
        st.session_state["selected_run"] = sel_run

        st.markdown("---")

        # Pipeline Quick Runner
        st.subheader("🚀 Run Optimization")
        scenario_select = st.selectbox(
            "Scenario Preset",
            options=["base", "price_spike", "charger_outage", "heavy_demand", "low_battery_fleet", "site_constraint"],
            index=0,
        )

        if st.button("⚡ Run Full Pipeline", type="primary", use_container_width=True):
            with st.spinner("Executing 7-agent pipeline..."):
                try:
                    cfg = load_config()
                    if scenario_select != "base":
                        from src.data.simulator import generate_all_tables
                        from src.data.scenarios import apply_scenario
                        t_base = generate_all_tables(cfg)
                        t_mod = apply_scenario(scenario_select, t_base, cfg)
                        res = run_pipeline(config=cfg, tables=t_mod)
                    else:
                        res = run_pipeline(config=cfg)

                    st.session_state["selected_run"] = "latest"
                    st.success("Optimization finished!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Execution failed: {e}")

        st.markdown("---")

        # Status & Diagnostic Info
        current_data = load_run(st.session_state["selected_run"])
        if current_data and current_data.run_meta:
            meta = current_data.run_meta
            st.caption(f"**Last Run:** {meta.get('timestamp', 'N/A')[:19]}")
            st.caption(f"**Method:** {current_data.kpis.get('solver', {}).get('method', 'CBC MILP')}")
            st.caption(f"**Status:** {current_data.kpis.get('solver', {}).get('status', 'Optimal')}")

            sources = meta.get("data_sources", {})
            st.markdown("**Data Sources:**")
            for table_name, src in list(sources.items())[:3]:
                st.caption(f"• `{table_name}`: {src}")

    # Top-level Page Navigation
    pages = {
        "📊 Fleet Overview": "src/dashboard/pages/01_overview.py",
        "⚡ Charging Schedule": "src/dashboard/pages/02_charging_schedule.py",
        "🎯 Trip Allocation": "src/dashboard/pages/03_trip_allocation.py",
        "💰 Cost Analytics": "src/dashboard/pages/04_cost_analytics.py",
        "💡 Recommendations": "src/dashboard/pages/05_recommendations.py",
        "🧪 Scenario Lab": "src/dashboard/pages/06_scenarios.py",
    }

    selected_page_name = st.sidebar.radio("Navigation", list(pages.keys()))

    # Render selected page
    if selected_page_name == "📊 Fleet Overview":
        from src.dashboard.pages import overview
        overview.render_page()
    elif selected_page_name == "⚡ Charging Schedule":
        from src.dashboard.pages import charging_schedule
        charging_schedule.render_page()
    elif selected_page_name == "🎯 Trip Allocation":
        from src.dashboard.pages import trip_allocation
        trip_allocation.render_page()
    elif selected_page_name == "💰 Cost Analytics":
        from src.dashboard.pages import cost_analytics
        cost_analytics.render_page()
    elif selected_page_name == "💡 Recommendations":
        from src.dashboard.pages import recommendations
        recommendations.render_page()
    elif selected_page_name == "🧪 Scenario Lab":
        from src.dashboard.pages import scenarios
        scenarios.render_page()

    # Footer
    st.markdown("---")
    st.markdown(
        "<div style='text-align: center; color: #6B7280; font-size: 0.8rem;'>"
        "AI Energy & EV Fleet Optimization Agent • Deterministic Cost Optimization Engine"
        "</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
