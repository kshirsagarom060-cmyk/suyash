"""Data access layer for loading pipeline run outputs and input tables with caching."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import pandas as pd

from src.common.io import read_json, read_jsonl
from src.data.loaders import load_tables


@dataclass
class RunData:
    run_id: str
    run_dir: Path
    charging: pd.DataFrame
    assignments: pd.DataFrame
    soc: pd.DataFrame
    site_load: pd.DataFrame
    kpis: dict[str, Any]
    recommendations: list[dict[str, Any]]
    agent_log: list[dict[str, Any]]
    run_meta: dict[str, Any]
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)


def get_outputs_dir() -> Path:
    """Returns root output directory."""
    return Path("data/outputs")


def list_runs() -> list[str]:
    """Lists all available run directories (latest first)."""
    out_dir = get_outputs_dir()
    if not out_dir.exists():
        return []
    
    runs = []
    # Add 'latest' if it exists and has kpis.json
    if (out_dir / "latest" / "kpis.json").exists():
        runs.append("latest")

    timestamped_runs = sorted(
        [p.name for p in out_dir.glob("run_*") if p.is_dir() and (p / "kpis.json").exists()],
        reverse=True
    )
    for r in timestamped_runs:
        if r not in runs:
            runs.append(r)
    return runs


def latest_run_dir() -> Optional[Path]:
    """Returns the path to the latest run directory."""
    out_dir = get_outputs_dir()
    latest_p = out_dir / "latest"
    if latest_p.exists() and (latest_p / "kpis.json").exists():
        return latest_p
    runs = list_runs()
    if runs:
        return out_dir / runs[0]
    return None


def load_run(run_identifier: str = "latest") -> Optional[RunData]:
    """Loads all tables, metrics, and metadata for a specific run."""
    out_dir = get_outputs_dir()
    run_path = out_dir / run_identifier
    if not run_path.exists():
        return None

    try:
        # Load output tables
        charging_csv = run_path / "charging_schedule.csv"
        assignments_csv = run_path / "assignments.csv"
        soc_csv = run_path / "soc_trajectory.csv"
        site_load_csv = run_path / "site_load.csv"

        charging = pd.read_csv(charging_csv) if charging_csv.exists() else pd.DataFrame()
        assignments = pd.read_csv(assignments_csv) if assignments_csv.exists() else pd.DataFrame()
        soc = pd.read_csv(soc_csv) if soc_csv.exists() else pd.DataFrame()
        site_load = pd.read_csv(site_load_csv) if site_load_csv.exists() else pd.DataFrame()

        kpis = read_json(run_path / "kpis.json") if (run_path / "kpis.json").exists() else {}
        recs = read_json(run_path / "recommendations.json") if (run_path / "recommendations.json").exists() else []
        agent_log = read_jsonl(run_path / "agent_log.jsonl") if (run_path / "agent_log.jsonl").exists() else []
        run_meta = read_json(run_path / "run_meta.json") if (run_path / "run_meta.json").exists() else {}

        # Load processed input tables for context
        processed_dir = Path("data/processed")
        input_tables = {}
        if processed_dir.exists() and (processed_dir / "vehicles.csv").exists():
            try:
                input_tables = load_tables(processed_dir)
            except Exception:
                pass

        return RunData(
            run_id=run_identifier,
            run_dir=run_path,
            charging=charging,
            assignments=assignments,
            soc=soc,
            site_load=site_load,
            kpis=kpis,
            recommendations=recs,
            agent_log=agent_log,
            run_meta=run_meta,
            tables=input_tables,
        )
    except Exception as e:
        print(f"Error loading run {run_identifier}: {e}")
        return None
