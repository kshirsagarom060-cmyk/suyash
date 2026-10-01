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
from src.dashboard.components import render_custom_css, render_hero_banner, render_data_source_badge
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
    # Top Hero Branding Header
    render_hero_banner(
        title="⚡ AI Energy & EV Fleet Optimization Agent",
        subtitle="Commercial EV Fleet Smart Charging, Route Feasibility & Time-of-Use Cost Minimization",
        badge_text="Operational • CBC MILP Engine",
    )

    # Sidebar Navigation & Execution Controls
    with st.sidebar:
        st.markdown(
            """
            <div style="padding: 10px 0 15px 0;">
                <div style="font-family: 'Outfit', sans-serif; font-size: 1.3rem; font-weight: 700; color: #FFFFFF;">
                    ⚡ Fleet Control Center
                </div>
                <div style="font-size: 0.8rem; color: #9CA3AF;">Smart Energy Management</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("---")

        # Available Runs Selector
        available_runs = list_runs()
        if "selected_run" not in st.session_state:
            st.session_state["selected_run"] = available_runs[0] if available_runs else "latest"

        sel_run = st.selectbox(
            "📁 Active Run Snapshot",
            options=available_runs if available_runs else ["latest"],
            index=0 if available_runs else 0,
        )
        st.session_state["selected_run"] = sel_run

        st.markdown("---")

        # Pipeline Quick Runner
        st.markdown("### ⚡ Quick Scenario Run")
        scenario_select = st.selectbox(
            "Select Scenario Preset",
            options=["base", "price_spike", "charger_outage", "heavy_demand", "low_battery_fleet", "site_constraint"],
            index=0,
            format_func=lambda s: {
                "base": "Base Normal Operations",
                "price_spike": "Tariff Price Spike (2.5x)",
                "charger_outage": "Depot Charger Outage",
                "heavy_demand": "Surge Delivery Demand (+30%)",
                "low_battery_fleet": "Low Fleet Battery State",
                "site_constraint": "Restricted Substation Limit",
            }.get(s, s),
        )

        if st.button("🚀 Optimize Fleet Now", type="primary", use_container_width=True):
            with st.spinner("Executing 7-agent optimization pipeline..."):
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
                    st.success("Optimization finished successfully!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Execution failed: {e}")

        st.markdown("---")

        # Status & Diagnostic Info
        current_data = load_run(st.session_state["selected_run"])
        if current_data and current_data.run_meta:
            meta = current_data.run_meta
            st.markdown(
                f"""
                <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 12px; font-size: 0.8rem;">
                    <div style="color: #9CA3AF; margin-bottom: 4px;">SYSTEM STATUS</div>
                    <div>• <b>Last Run:</b> {meta.get('timestamp', 'N/A')[:19]}</div>
                    <div>• <b>Solver:</b> {current_data.kpis.get('solver', {}).get('method', 'CBC MILP')}</div>
                    <div>• <b>Status:</b> <span style="color: #10B981;">{current_data.kpis.get('solver', {}).get('status', 'Optimal')}</span></div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # Top-level Page Navigation
    pages = {
        "📊 Fleet Overview": "src/dashboard/pages/01_overview.py",
        "⚡ Charging Schedule": "src/dashboard/pages/02_charging_schedule.py",
        "🎯 Trip Allocation": "src/dashboard/pages/03_trip_allocation.py",
        "💰 Cost Analytics": "src/dashboard/pages/04_cost_analytics.py",
        "💡 Recommendations": "src/dashboard/pages/05_recommendations.py",
        "🧪 Scenario Lab": "src/dashboard/pages/06_scenarios.py",
    }

    selected_page_name = st.sidebar.radio("Navigation Menu", list(pages.keys()))

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

    # Clean Modern Footer
    st.markdown("---")
    st.markdown(
        "<div style='text-align: center; color: #64748B; font-size: 0.82rem; padding: 12px 0;'>"
        "⚡ AI Energy & EV Fleet Optimization Agent • Deterministic Cost Optimization Engine"
        "</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
