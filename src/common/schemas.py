"""Shared schemas and dataclasses for AI Energy & EV Fleet Optimizer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional
import pandas as pd


@dataclass
class AgentContext:
    """Shared execution context passed across agents."""
    config: Any  # Config dataclass
    tables: dict[str, pd.DataFrame]  # Input tables by name
    results: dict[str, Any] = field(default_factory=dict)  # Outputs of earlier agents


@dataclass
class AgentResult:
    """Standardized output structure for all agents."""
    agent: str
    status: Literal["ok", "warning", "failed"]
    outputs: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    duration_s: float = 0.0

    def get(self, key: str, default: Any = None) -> Any:
        """Helper to allow dict-like access."""
        if hasattr(self, key):
            return getattr(self, key)
        return default


@dataclass
class OptimizationInput:
    """Consolidated inputs required by the optimization engine."""
    vehicles: pd.DataFrame
    trips: pd.DataFrame
    eligibility: pd.DataFrame
    chargers: pd.DataFrame
    charger_capacity: pd.DataFrame
    site_capacity: pd.DataFrame
    prices: pd.DataFrame
    cost_params: dict[str, Any]
    n_slots: int
    slot_hours: float


@dataclass
class Plan:
    """Represents a full operational plan (baseline or optimized)."""
    name: str  # "baseline" | "optimized" | "heuristic"
    charging: pd.DataFrame  # vehicle_id, charger_id, charger_type, slot, timestamp, power_kw_grid, energy_to_battery_kwh, price_per_kwh, plan
    assignments: pd.DataFrame  # trip_id, vehicle_id, served, required_energy_kwh, departure_soc_pct, plan
    soc: pd.DataFrame  # vehicle_id, slot, timestamp, soc_pct, energy_kwh, on_trip, plan
    site_load: pd.DataFrame  # slot, timestamp, site_kw, site_limit_kw, price_per_kwh, plan
    solver_info: dict[str, Any] = field(default_factory=dict)


@dataclass
class Violation:
    """Operational constraint violation detected by the plan validator."""
    type: str
    vehicle_id: Optional[str] = None
    trip_id: Optional[str] = None
    slot: Optional[int] = None
    amount: float = 0.0
    message: str = ""


@dataclass
class Recommendation:
    """Actionable recommendation or alert produced by the recommendation agent."""
    id: str
    severity: Literal["info", "warning", "critical"]
    category: str
    title: str
    detail: str
    vehicle_id: Optional[str] = None
    trip_id: Optional[str] = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataSourceReport:
    """Audit report of data sources used (simulated vs real)."""
    sources: dict[str, str] = field(default_factory=dict)
    timestamp: str = ""
    seed: int = 42
