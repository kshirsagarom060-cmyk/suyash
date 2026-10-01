"""Reusable UI components, CSS design tokens, KPI cards, and data export widgets."""

from __future__ import annotations

from typing import Any, Optional
import pandas as pd
import streamlit as st


def render_custom_css() -> None:
    """Injects modern, premium dark-mode styling and glassmorphism cards."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        
        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }
        
        /* Metric card styling */
        .kpi-card {
            background: linear-gradient(135deg, rgba(31, 41, 55, 0.7), rgba(17, 24, 39, 0.9));
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            padding: 18px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
            backdrop-filter: blur(10px);
            margin-bottom: 12px;
            transition: transform 0.2s ease, border-color 0.2s ease;
        }
        .kpi-card:hover {
            transform: translateY(-2px);
            border-color: rgba(59, 130, 246, 0.4);
        }
        .kpi-title {
            color: #9CA3AF;
            font-size: 0.85rem;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }
        .kpi-value {
            color: #F9FAFB;
            font-size: 1.8rem;
            font-weight: 700;
            line-height: 1.2;
            margin-bottom: 4px;
        }
        .kpi-subtext {
            color: #10B981;
            font-size: 0.8rem;
            font-weight: 500;
        }
        
        /* Alert boxes */
        .alert-card {
            padding: 14px 18px;
            border-radius: 10px;
            margin-bottom: 12px;
            border-left: 4px solid;
            font-size: 0.9rem;
        }
        .alert-critical {
            background: rgba(239, 68, 68, 0.12);
            border-left-color: #EF4444;
            color: #FCA5A5;
        }
        .alert-warning {
            background: rgba(245, 158, 11, 0.12);
            border-left-color: #F59E0B;
            color: #FCD34D;
        }
        .alert-info {
            background: rgba(59, 130, 246, 0.12);
            border-left-color: #3B82F6;
            color: #93C5FD;
        }
        
        /* Badges */
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
            margin-right: 6px;
        }
        .badge-simulated {
            background: rgba(107, 114, 128, 0.3);
            color: #D1D5DB;
        }
        .badge-real {
            background: rgba(16, 185, 129, 0.25);
            color: #34D399;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_kpi_card(title: str, value: str, subtext: Optional[str] = None, delta_color: str = "#10B981") -> None:
    """Renders a styled KPI card."""
    sub_html = f'<div class="kpi-subtext" style="color: {delta_color};">{subtext}</div>' if subtext else ""
    st.markdown(
        f"""
        <div class="kpi-card">
            <div class="kpi-title">{title}</div>
            <div class="kpi-value">{value}</div>
            {sub_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_alert_box(title: str, message: str, severity: str = "info", details: Optional[str] = None) -> None:
    """Renders a categorized alert banner."""
    sev = severity.lower()
    icon = "🚨" if sev == "critical" else ("⚠️" if sev == "warning" else "ℹ️")
    det_html = f"<div style='margin-top: 6px; font-size: 0.8rem; opacity: 0.85;'>{details}</div>" if details else ""
    st.markdown(
        f"""
        <div class="alert-card alert-{sev}">
            <strong>{icon} {title}</strong>
            <div>{message}</div>
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
        st.markdown(f'<span class="badge badge-real">● Real: {source_name.split(":", 1)[1]}</span>', unsafe_allow_html=True)
    else:
        st.markdown(f'<span class="badge badge-simulated">○ {source_name.capitalize()}</span>', unsafe_allow_html=True)
