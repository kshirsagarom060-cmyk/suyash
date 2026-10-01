"""Plotly chart generation module for EV Fleet Energy Optimizer dashboard."""

from __future__ import annotations

from typing import Any, Optional
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

# Curated harmonious high-contrast modern palette
COLOR_OPTIMIZED = "#38BDF8"   # Electric Sky Blue
COLOR_BASELINE = "#94A3B8"    # Cool Slate Grey
COLOR_OFFPEAK = "#10B981"     # Emerald Green
COLOR_SHOULDER = "#F59E0B"    # Amber Gold
COLOR_PEAK = "#EF4444"        # Crimson Rose
COLOR_DC = "#8B5CF6"          # Purple Violet
COLOR_AC = "#06B6D4"          # Cyan Teal
COLOR_BG = "#0B0F19"          # Dark Obsidian
COLOR_CARD = "#111827"        # Dark Card
GRID_COLOR = "rgba(255, 255, 255, 0.06)"


def _apply_theme(fig: go.Figure, title: str, x_title: Optional[str] = None, y_title: Optional[str] = None) -> go.Figure:
    """Applies modern dark aesthetic, soft gridlines, and typography to any figure."""
    fig.update_layout(
        template="plotly_dark",
        title={"text": f"<b>{title}</b>", "font": {"family": "Outfit, Inter, sans-serif", "size": 15, "color": "#F9FAFB"}},
        paper_bgcolor="rgba(11, 15, 25, 0.7)",
        plot_bgcolor="rgba(11, 15, 25, 0.7)",
        font={"family": "Inter, sans-serif", "color": "#9CA3AF"},
        xaxis={
            "title": x_title,
            "gridcolor": GRID_COLOR,
            "zerolinecolor": GRID_COLOR,
            "showline": True,
            "linecolor": GRID_COLOR,
        } if x_title else {"gridcolor": GRID_COLOR, "zerolinecolor": GRID_COLOR},
        yaxis={
            "title": y_title,
            "gridcolor": GRID_COLOR,
            "zerolinecolor": GRID_COLOR,
            "showline": True,
            "linecolor": GRID_COLOR,
        } if y_title else {"gridcolor": GRID_COLOR, "zerolinecolor": GRID_COLOR},
        margin={"l": 45, "r": 25, "t": 48, "b": 40},
        hoverlabel={"bgcolor": "#1F2937", "font_size": 12, "font_family": "Inter, sans-serif", "font_color": "#F9FAFB"},
    )
    return fig


def empty_fig(title: str = "No data available") -> go.Figure:
    """Returns an empty figure with a clean centered annotation."""
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=COLOR_BG,
        plot_bgcolor=COLOR_BG,
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[{
            "text": title,
            "xref": "paper",
            "yref": "paper",
            "showarrow": False,
            "font": {"size": 15, "color": "#9CA3AF", "family": "Inter, sans-serif"},
        }],
        margin={"l": 20, "r": 20, "t": 30, "b": 20},
    )
    return fig


def soc_bar(vehicles_df: pd.DataFrame, reserve_pct: float = 15.0, ceiling_pct: float = 90.0) -> go.Figure:
    """Sorted bar chart of vehicle initial SOC with reserve and ceiling reference lines."""
    if vehicles_df is None or vehicles_df.empty or "current_soc_pct" not in vehicles_df.columns:
        return empty_fig("No vehicle SOC data")

    df = vehicles_df.copy().sort_values("current_soc_pct")
    colors = [
        COLOR_PEAK if soc < reserve_pct else COLOR_OFFPEAK
        for soc in df["current_soc_pct"]
    ]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["vehicle_id"],
        y=df["current_soc_pct"],
        marker_color=colors,
        text=[f"{val:.1f}%" for val in df["current_soc_pct"]],
        textposition="auto",
        name="Current SOC (%)",
        hovertemplate="<b>%{x}</b><br>State of Charge: <b>%{y:.1f}%</b><extra></extra>",
    ))

    fig.add_hline(
        y=reserve_pct,
        line_dash="dash",
        line_color=COLOR_PEAK,
        annotation_text=f"Reserve Limit ({reserve_pct}%)",
        annotation_position="bottom right",
    )
    fig.add_hline(
        y=ceiling_pct,
        line_dash="dash",
        line_color=COLOR_SHOULDER,
        annotation_text=f"Health Ceiling ({ceiling_pct}%)",
        annotation_position="top right",
    )

    fig.update_layout(yaxis_range=[0, 105])
    return _apply_theme(fig, "Fleet Initial State of Charge (SOC) Distribution", "Vehicle ID", "State of Charge (%)")


def demand_vs_supply(demand_by_hour: pd.DataFrame, n_usable_vehicles: int) -> go.Figure:
    """Hourly stacked bar chart of trip demand by priority vs total usable fleet supply."""
    if demand_by_hour is None or demand_by_hour.empty:
        return empty_fig("No hourly demand data")

    fig = go.Figure()
    hours = demand_by_hour["hour"]
    
    if "p1" in demand_by_hour.columns:
        fig.add_trace(go.Bar(x=hours, y=demand_by_hour["p1"], name="Priority 1 (Critical)", marker_color=COLOR_PEAK))
    if "p2" in demand_by_hour.columns:
        fig.add_trace(go.Bar(x=hours, y=demand_by_hour["p2"], name="Priority 2 (Normal)", marker_color=COLOR_SHOULDER))
    if "p3" in demand_by_hour.columns:
        fig.add_trace(go.Bar(x=hours, y=demand_by_hour["p3"], name="Priority 3 (Flexible)", marker_color=COLOR_OFFPEAK))

    fig.add_hline(
        y=n_usable_vehicles,
        line_dash="dot",
        line_color="#60A5FA",
        annotation_text=f"Usable Fleet ({n_usable_vehicles} EVs)",
        annotation_position="top left",
    )

    fig.update_layout(barmode="stack", legend={"orientation": "h", "y": 1.12, "x": 0.5, "xanchor": "center"})
    return _apply_theme(fig, "Hourly Trip Demand vs Available Fleet Supply", "Hour of Day", "Trip Departures (Count)")


def charging_gantt(
    charging_df: pd.DataFrame,
    assignments_df: pd.DataFrame,
    trips_df: pd.DataFrame,
    plan: str = "optimized",
) -> go.Figure:
    """Visualizes vehicle activity timeline: charging sessions colored by charger type and trips overlaid."""
    if charging_df is None or charging_df.empty:
        return empty_fig("No charging schedule data")

    plan_chg = charging_df[charging_df["plan"] == plan] if "plan" in charging_df.columns else charging_df
    plan_chg = plan_chg[plan_chg["power_kw_grid"] > 1e-4]

    fig = go.Figure()
    vehicles = sorted(plan_chg["vehicle_id"].unique()) if not plan_chg.empty else []

    # Charging segments
    for ch_type, col in [("AC", COLOR_AC), ("DC", COLOR_DC)]:
        type_df = plan_chg[plan_chg["charger_type"] == ch_type]
        for _, row in type_df.iterrows():
            s = int(row["slot"])
            v = str(row["vehicle_id"])
            p_kw = float(row["power_kw_grid"])
            fig.add_trace(go.Bar(
                x=[0.25],
                y=[v],
                base=[s * 0.25],
                orientation="h",
                marker_color=col,
                name=f"{ch_type} Charging",
                showlegend=(v == vehicles[0] if vehicles else False),
                hovertemplate=f"<b>{v}</b><br>{ch_type} Charging: {p_kw:.1f} kW<br>Slot: {s}<extra></extra>",
            ))

    # Overlaid trip segments
    if assignments_df is not None and not assignments_df.empty and trips_df is not None and not trips_df.empty:
        plan_ass = assignments_df[assignments_df["plan"] == plan] if "plan" in assignments_df.columns else assignments_df
        served = plan_ass[plan_ass["served"] == True]
        trips_lookup = trips_df.set_index("trip_id")

        for _, ass in served.iterrows():
            t_id = ass["trip_id"]
            v = ass["vehicle_id"]
            if t_id in trips_lookup.index and pd.notna(v) and v:
                dep_s = int(trips_lookup.loc[t_id]["departure_slot"])
                ret_s = int(trips_lookup.loc[t_id]["return_slot"])
                dur_h = (ret_s - dep_s) * 0.25
                fig.add_trace(go.Bar(
                    x=[dur_h],
                    y=[v],
                    base=[dep_s * 0.25],
                    orientation="h",
                    marker_color="#F97316",
                    name="On Trip",
                    showlegend=False,
                    hovertemplate=f"<b>{v}</b><br>Trip {t_id}<br>Duration: {dur_h:.1f}h<extra></extra>",
                ))

    fig.update_layout(barmode="overlay")
    return _apply_theme(fig, f"Fleet Charging Schedule & Trip Execution ({plan.capitalize()})", "Timeline (Hours into Horizon)", "Vehicle")


def price_band(tariffs_df: pd.DataFrame) -> go.Figure:
    """Step-line tariff price profile with colored background periods."""
    if tariffs_df is None or tariffs_df.empty:
        return empty_fig("No tariff data")

    fig = go.Figure()
    hours = tariffs_df["slot"] * 0.25
    prices = tariffs_df["price_per_kwh"]

    fig.add_trace(go.Scatter(
        x=hours,
        y=prices,
        mode="lines",
        line={"shape": "hv", "color": "#FACC15", "width": 3},
        name="Price / kWh",
        hovertemplate="Time: %{x:.2f}h<br>Tariff: <b>₹%{y:.2f}/kWh</b><extra></extra>",
    ))

    return _apply_theme(fig, "Time-of-Use Electricity Tariff Schedule", "Planning Horizon (Hours)", "Price (₹ / kWh)")


def site_load(site_load_df: pd.DataFrame, plan_toggle: str = "compare", site_limit_kw: float = 90.0) -> go.Figure:
    """Area chart / comparative lines showing aggregate site load against the site power ceiling."""
    if site_load_df is None or site_load_df.empty:
        return empty_fig("No site load data")

    fig = go.Figure()
    
    if plan_toggle in ["baseline", "compare"]:
        base_df = site_load_df[site_load_df["plan"] == "baseline"] if "plan" in site_load_df.columns else site_load_df
        if not base_df.empty:
            fig.add_trace(go.Scatter(
                x=base_df["slot"] * 0.25,
                y=base_df["site_kw"],
                mode="lines",
                name="Baseline Site Power",
                line={"color": COLOR_BASELINE, "width": 2, "dash": "dash"},
                hovertemplate="Baseline: %{y:.1f} kW<extra></extra>",
            ))

    if plan_toggle in ["optimized", "compare"]:
        opt_df = site_load_df[site_load_df["plan"] == "optimized"] if "plan" in site_load_df.columns else site_load_df
        if not opt_df.empty:
            fig.add_trace(go.Scatter(
                x=opt_df["slot"] * 0.25,
                y=opt_df["site_kw"],
                mode="lines",
                fill="tozeroy",
                fillcolor="rgba(56, 189, 248, 0.2)",
                name="Optimized Site Power",
                line={"color": COLOR_OPTIMIZED, "width": 3},
                hovertemplate="Optimized: <b>%{y:.1f} kW</b><extra></extra>",
            ))

    fig.add_hline(
        y=site_limit_kw,
        line_dash="dot",
        line_color=COLOR_PEAK,
        annotation_text=f"Depot Grid Limit ({site_limit_kw:.1f} kW)",
        annotation_position="top right",
    )

    fig.update_layout(legend={"orientation": "h", "y": 1.12, "x": 0.5, "xanchor": "center"})
    return _apply_theme(fig, "Aggregate Depot Power Demand vs Site Capacity Limit", "Horizon Timeline (Hours)", "Grid Power Demand (kW)")


def vehicle_soc_trajectory(
    soc_df: pd.DataFrame,
    vehicle_id: str,
    plan: str = "optimized",
    reserve_kwh: Optional[float] = None,
    ceiling_kwh: Optional[float] = None,
    charging_df: Optional[pd.DataFrame] = None,
) -> go.Figure:
    """Individual vehicle battery trajectory over time with charging overlay."""
    if soc_df is None or soc_df.empty:
        return empty_fig("No SOC trajectory data")

    v_soc = soc_df[(soc_df["vehicle_id"] == vehicle_id) & (soc_df["plan"] == plan)] if "plan" in soc_df.columns else soc_df[soc_df["vehicle_id"] == vehicle_id]
    if v_soc.empty:
        return empty_fig(f"No SOC data for vehicle {vehicle_id}")

    fig = go.Figure()
    hours = v_soc["slot"] * 0.25

    fig.add_trace(go.Scatter(
        x=hours,
        y=v_soc["energy_kwh"],
        mode="lines+markers",
        line={"color": COLOR_OPTIMIZED, "width": 3, "shape": "spline"},
        marker={"size": 4, "color": COLOR_OPTIMIZED},
        name=f"{vehicle_id} Energy (kWh)",
        hovertemplate="Time: %{x:.2f}h<br>Energy: <b>%{y:.2f} kWh</b><extra></extra>",
    ))

    if reserve_kwh is not None:
        fig.add_hline(y=reserve_kwh, line_dash="dash", line_color=COLOR_PEAK, annotation_text=f"Reserve ({reserve_kwh:.1f} kWh)")
    if ceiling_kwh is not None:
        fig.add_hline(y=ceiling_kwh, line_dash="dash", line_color=COLOR_SHOULDER, annotation_text=f"Ceiling ({ceiling_kwh:.1f} kWh)")

    return _apply_theme(fig, f"Vehicle {vehicle_id} Battery Trajectory ({plan.capitalize()})", "Timeline (Hours)", "Stored Energy (kWh)")


def cost_breakdown(kpis: dict[str, Any]) -> go.Figure:
    """Grouped bar chart comparing baseline vs optimized cost components."""
    if not kpis or "baseline" not in kpis or "optimized" not in kpis:
        return empty_fig("No cost evaluation data")

    b = kpis["baseline"]
    o = kpis["optimized"]

    categories = ["Electricity Energy", "Peak Demand", "Battery Wear", "Total Operating"]
    b_vals = [b.get("energy_cost", 0), b.get("demand_cost", 0), b.get("wear_cost", 0), b.get("operating_cost", 0)]
    o_vals = [o.get("energy_cost", 0), o.get("demand_cost", 0), o.get("wear_cost", 0), o.get("operating_cost", 0)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Baseline (Unmanaged)",
        x=categories,
        y=b_vals,
        marker_color=COLOR_BASELINE,
        text=[f"₹{v:,.0f}" for v in b_vals],
        textposition="auto",
    ))
    fig.add_trace(go.Bar(
        name="Optimized (Smart)",
        x=categories,
        y=o_vals,
        marker_color=COLOR_OPTIMIZED,
        text=[f"₹{v:,.0f}" for v in o_vals],
        textposition="auto",
    ))

    fig.update_layout(barmode="group", legend={"orientation": "h", "y": 1.12, "x": 0.5, "xanchor": "center"})
    return _apply_theme(fig, "Cost Category Comparison: Baseline vs Optimized", "", "Cost Amount (INR ₹)")


def energy_by_period(kpis: dict[str, Any]) -> go.Figure:
    """Stacked bar chart of energy consumption by tariff band."""
    if not kpis or "baseline" not in kpis or "optimized" not in kpis:
        return empty_fig("No period energy data")

    b_per = kpis["baseline"].get("energy_by_period_kwh", {})
    o_per = kpis["optimized"].get("energy_by_period_kwh", {})

    plans = ["Baseline", "Optimized"]
    offpeaks = [b_per.get("offpeak", 0), o_per.get("offpeak", 0)]
    shoulders = [b_per.get("shoulder", 0), o_per.get("shoulder", 0)]
    peaks = [b_per.get("peak", 0), o_per.get("peak", 0)]

    fig = go.Figure()
    fig.add_trace(go.Bar(name="Off-Peak (Cheapest)", x=plans, y=offpeaks, marker_color=COLOR_OFFPEAK))
    fig.add_trace(go.Bar(name="Shoulder", x=plans, y=shoulders, marker_color=COLOR_SHOULDER))
    fig.add_trace(go.Bar(name="Peak (Expensive)", x=plans, y=peaks, marker_color=COLOR_PEAK))

    fig.update_layout(barmode="stack", legend={"orientation": "h", "y": 1.12, "x": 0.5, "xanchor": "center"})
    return _apply_theme(fig, "Energy Charged by Tariff Window (kWh)", "", "Energy Charged (kWh)")


def charger_utilization(kpis: dict[str, Any]) -> go.Figure:
    """Bar chart comparing AC vs DC charger occupancy utilization."""
    if not kpis or "optimized" not in kpis:
        return empty_fig("No utilization data")

    util = kpis["optimized"].get("charger_utilization", {})
    fig = go.Figure(go.Bar(
        x=["AC Charging", "DC Fast Charging"],
        y=[util.get("AC", 0) * 100.0, util.get("DC", 0) * 100.0],
        marker_color=[COLOR_AC, COLOR_DC],
        text=[f"{util.get('AC', 0)*100.0:.1f}%", f"{util.get('DC', 0)*100.0:.1f}%"],
        textposition="auto",
    ))

    fig.update_layout(yaxis_range=[0, 105])
    return _apply_theme(fig, "Charger Utilization Rate (%)", "", "Occupancy Utilization (%)")


def margin_hist(
    assignments_df: pd.DataFrame,
    trips_df: pd.DataFrame,
    soc_df: pd.DataFrame,
    eligibility_df: pd.DataFrame,
) -> go.Figure:
    """Histogram of vehicle battery departure buffers (margin above reserve)."""
    if assignments_df is None or assignments_df.empty or trips_df is None or trips_df.empty:
        return empty_fig("No margin data available")

    served = assignments_df[assignments_df["served"] == True]
    if served.empty:
        return empty_fig("No served trips to display")

    margins = []
    trips_lookup = trips_df.set_index("trip_id")
    for _, row in served.iterrows():
        t_id = row["trip_id"]
        v_id = row["vehicle_id"]
        if pd.isna(v_id) or not v_id or t_id not in trips_lookup.index:
            continue
        dep_s = int(trips_lookup.loc[t_id]["departure_slot"])
        v_soc = soc_df[(soc_df["vehicle_id"] == v_id) & (soc_df["slot"] == dep_s)]
        if not v_soc.empty and eligibility_df is not None:
            e_dep = float(v_soc.iloc[0]["energy_kwh"])
            m = eligibility_df[(eligibility_df["vehicle_id"] == v_id) & (eligibility_df["trip_id"] == t_id)]
            if not m.empty:
                req_dep = float(m.iloc[0]["required_departure_kwh"])
                margins.append(e_dep - req_dep)

    if not margins:
        return empty_fig("No departure margin records")

    fig = go.Figure(go.Histogram(
        x=margins,
        nbinsx=15,
        marker_color=COLOR_OPTIMIZED,
        opacity=0.85,
    ))

    return _apply_theme(fig, "Departure Safety Buffer Distribution (kWh above Minimum Reserve)", "Departure Buffer (kWh)", "Number of Trips")


def trip_timeline(assignments_df: pd.DataFrame, trips_df: pd.DataFrame, plan: str = "optimized") -> go.Figure:
    """Timeline chart of trip assignments by vehicle."""
    if assignments_df is None or assignments_df.empty or trips_df is None or trips_df.empty:
        return empty_fig("No trip assignment data")

    plan_ass = assignments_df[assignments_df["plan"] == plan] if "plan" in assignments_df.columns else assignments_df
    served = plan_ass[plan_ass["served"] == True]
    trips_lookup = trips_df.set_index("trip_id")

    fig = go.Figure()
    for _, ass in served.iterrows():
        t_id = ass["trip_id"]
        v = ass["vehicle_id"]
        if t_id in trips_lookup.index and pd.notna(v) and v:
            t_row = trips_lookup.loc[t_id]
            dep_h = int(t_row["departure_slot"]) * 0.25
            dur_h = (int(t_row["return_slot"]) - int(t_row["departure_slot"])) * 0.25
            pri = int(t_row["priority"])
            color = COLOR_PEAK if pri == 1 else (COLOR_SHOULDER if pri == 2 else COLOR_OFFPEAK)

            fig.add_trace(go.Bar(
                x=[dur_h],
                y=[v],
                base=[dep_h],
                orientation="h",
                marker_color=color,
                name=f"Priority {pri}",
                showlegend=False,
                hovertemplate=f"<b>Trip {t_id}</b><br>Vehicle: {v}<br>Start: {dep_h:.1f}h<br>Duration: {dur_h:.1f}h<br>Priority: {pri}<extra></extra>",
            ))

    return _apply_theme(fig, f"Vehicle-to-Trip Assignment Timeline ({plan.capitalize()})", "Horizon Time (Hours)", "Assigned Vehicle")


def tradeoff_curve(tradeoff_data: list[dict[str, Any]]) -> go.Figure:
    """Sensitivity analysis trade-off curve."""
    if not tradeoff_data:
        return empty_fig("No sensitivity data")

    df = pd.DataFrame(tradeoff_data)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["parameter_value"],
        y=df["total_cost"],
        mode="lines+markers",
        line={"color": COLOR_OPTIMIZED, "width": 3, "shape": "spline"},
        marker={"size": 6, "color": COLOR_OPTIMIZED},
        name="Total Cost",
        hovertemplate="Parameter: %{x}<br>Total Cost: <b>₹%{y:,.0f}</b><extra></extra>",
    ))

    return _apply_theme(fig, "Parameter Sensitivity Trade-off Curve", "Parameter Value", "Total Operating Cost (INR ₹)")
