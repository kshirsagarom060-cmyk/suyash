"""Reusable UI components, CSS design tokens, KPI cards, and data export widgets."""

from __future__ import annotations

from typing import Any, Optional
import pandas as pd
import streamlit as st


def render_custom_css() -> None:
    """Injects modern, premium dark-mode styling, glowing accents, and glassmorphic cards."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@500;600;700;800&display=swap');
        
        :root {
            --bg-base: #0B0F19;
            --bg-card: rgba(17, 24, 39, 0.75);
            --border-subtle: rgba(255, 255, 255, 0.08);
            --border-hover: rgba(59, 130, 246, 0.4);
            --text-primary: #F9FAFB;
            --text-secondary: #9CA3AF;
            --accent-blue: #38BDF8;
            --accent-indigo: #6366F1;
            --accent-emerald: #10B981;
            --accent-amber: #F59E0B;
            --accent-rose: #EF4444;
        }

        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            color: var(--text-primary);
        }

        h1, h2, h3, .brand-font {
            font-family: 'Outfit', sans-serif !important;
            letter-spacing: -0.02em;
        }

        /* Top Hero Header */
        .hero-banner {
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.7), rgba(15, 23, 42, 0.85));
            border: 1px solid var(--border-subtle);
            border-radius: 16px;
            padding: 22px 28px;
            margin-bottom: 24px;
            box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
            backdrop-filter: blur(12px);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }
        .hero-title {
            font-size: 1.6rem;
            font-weight: 700;
            color: #FFFFFF;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .hero-subtitle {
            font-size: 0.9rem;
            color: var(--text-secondary);
            margin-top: 4px;
        }

        /* Metric KPI card styling */
        .kpi-card {
            background: linear-gradient(145deg, rgba(30, 41, 59, 0.6), rgba(15, 23, 42, 0.85));
            border: 1px solid var(--border-subtle);
            border-radius: 14px;
            padding: 20px 22px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
            backdrop-filter: blur(12px);
            margin-bottom: 14px;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
        }
        .kpi-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, #38BDF8, #6366F1);
            opacity: 0.8;
        }
        .kpi-card:hover {
            transform: translateY(-3px);
            border-color: var(--border-hover);
            box-shadow: 0 12px 30px rgba(56, 189, 248, 0.15);
        }
        .kpi-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        .kpi-title {
            color: var(--text-secondary);
            font-size: 0.82rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }
        .kpi-icon {
            font-size: 1.15rem;
            opacity: 0.9;
        }
        .kpi-value {
            font-family: 'Outfit', sans-serif;
            color: #FFFFFF;
            font-size: 1.95rem;
            font-weight: 700;
            line-height: 1.2;
            margin-bottom: 6px;
            letter-spacing: -0.01em;
        }
        .kpi-subtext {
            font-size: 0.82rem;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        
        /* Alert boxes */
        .alert-card {
            padding: 16px 20px;
            border-radius: 12px;
            margin-bottom: 14px;
            border-left: 4px solid;
            font-size: 0.92rem;
            backdrop-filter: blur(8px);
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        }
        .alert-critical {
            background: rgba(239, 68, 68, 0.12);
            border-left-color: #EF4444;
            color: #FECACA;
        }
        .alert-warning {
            background: rgba(245, 158, 11, 0.12);
            border-left-color: #F59E0B;
            color: #FDE68A;
        }
        .alert-info {
            background: rgba(59, 130, 246, 0.12);
            border-left-color: #38BDF8;
            color: #BAE6FD;
        }
        
        /* Badges & Status Pills */
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: 0.02em;
        }
        .badge-simulated {
            background: rgba(107, 114, 128, 0.25);
            color: #E5E7EB;
            border: 1px solid rgba(156, 163, 175, 0.3);
        }
        .badge-real {
            background: rgba(16, 185, 129, 0.2);
            color: #34D399;
            border: 1px solid rgba(52, 211, 153, 0.4);
        }
        .pulse-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            display: inline-block;
            background: #10B981;
            box-shadow: 0 0 8px #10B981;
        }

        /* Modern Progress Bar */
        .soc-bar-container {
            width: 100%;
            height: 8px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 999px;
            overflow: hidden;
            margin-top: 4px;
        }
        .soc-bar-fill {
            height: 100%;
            border-radius: 999px;
            transition: width 0.3s ease;
        }

        /* Streamlit Native Overrides */
        .stButton>button {
            border-radius: 10px !important;
            font-weight: 600 !important;
            letter-spacing: 0.02em !important;
            transition: all 0.2s ease !important;
        }
        .stButton>button[kind="primary"] {
            background: linear-gradient(135deg, #38BDF8, #6366F1) !important;
            border: none !important;
            color: #FFFFFF !important;
            box-shadow: 0 4px 14px rgba(56, 189, 248, 0.3) !important;
        }
        .stButton>button[kind="primary"]:hover {
            transform: translateY(-1px) !important;
            box-shadow: 0 6px 20px rgba(56, 189, 248, 0.5) !important;
        }

        /* Sidebar Styling */
        [data-testid="stSidebar"] {
            background-color: #0E131F !important;
            border-right: 1px solid var(--border-subtle) !important;
        }
        [data-testid="stSidebar"] hr {
            margin: 1rem 0 !important;
            border-color: var(--border-subtle) !important;
        }

        /* Dataframe Styling */
        [data-testid="stDataFrame"] {
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            overflow: hidden;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_hero_banner(title: str, subtitle: str, badge_text: str = "Deterministic Solver Active") -> None:
    """Renders a top hero banner with branding and operational status."""
    st.markdown(
        f"""
        <div class="hero-banner">
            <div>
                <div class="hero-title">{title}</div>
                <div class="hero-subtitle">{subtitle}</div>
            </div>
            <div>
                <span class="badge badge-real">
                    <span class="pulse-dot"></span> {badge_text}
                </span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpi_card(
    title: str,
    value: str,
    subtext: Optional[str] = None,
    delta_color: str = "#10B981",
    icon: str = "⚡",
) -> None:
    """Renders a modern, styled KPI card with icon, glow, and status delta."""
    sub_html = f'<div class="kpi-subtext" style="color: {delta_color};">{subtext}</div>' if subtext else ""
    st.markdown(
        f"""
        <div class="kpi-card">
            <div class="kpi-header">
                <span class="kpi-title">{title}</span>
                <span class="kpi-icon">{icon}</span>
            </div>
            <div class="kpi-value">{value}</div>
            {sub_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_alert_box(title: str, message: str, severity: str = "info", details: Optional[str] = None) -> None:
    """Renders a categorized, high-contrast alert banner."""
    sev = severity.lower()
    icon = "🚨" if sev == "critical" else ("⚠️" if sev == "warning" else "ℹ️")
    det_html = f"<div style='margin-top: 6px; font-size: 0.82rem; opacity: 0.9;'>{details}</div>" if details else ""
    st.markdown(
        f"""
        <div class="alert-card alert-{sev}">
            <strong>{icon} {title}</strong>
            <div style="margin-top: 4px;">{message}</div>
            {det_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def download_csv_button(df: pd.DataFrame, filename: str, label: str = "📥 Download CSV") -> None:
    """Renders a styled CSV download button."""
    if df is not None and not df.empty:
        csv_data = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label=label,
            data=csv_data,
            file_name=filename,
            mime="text/csv",
            use_container_width=True,
        )


def render_data_source_badge(source_name: str) -> None:
    """Renders a badge showing simulated vs real data source."""
    if source_name.startswith("real:"):
        st.markdown(f'<span class="badge badge-real"><span class="pulse-dot"></span> Real: {source_name.split(":", 1)[1]}</span>', unsafe_allow_html=True)
    else:
        st.markdown(f'<span class="badge badge-simulated">○ {source_name.capitalize()} Baseline</span>', unsafe_allow_html=True)
