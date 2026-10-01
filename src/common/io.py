"""I/O utilities for exporting outputs, copying latest runs, and serializing JSON/CSV."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
import pandas as pd

from src.common.schemas import Plan


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        return super().default(obj)


def write_json(data: Any, path: Path) -> None:
    """Writes JSON data with indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, cls=CustomJSONEncoder)


def read_json(path: Path) -> Any:
    """Reads JSON data from file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_jsonl(records: list[dict[str, Any]], path: Path) -> None:
    """Writes list of dicts as JSON Lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, cls=CustomJSONEncoder) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Reads JSON Lines file."""
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def export_pipeline_results(
    output_dir: Path,
    baseline_plan: Plan,
    optimized_plan: Plan,
    kpis: dict[str, Any],
    recommendations: list[dict[str, Any]],
    agent_logs: list[dict[str, Any]],
    run_meta: dict[str, Any],
    copy_to_latest: bool = True,
) -> Path:
    """Writes all output files specified in 00_shared_contract.md and syncs to latest/."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. charging_schedule.csv (combined baseline + optimized)
    charging_combined = pd.concat([baseline_plan.charging, optimized_plan.charging], ignore_index=True)
    charging_combined.to_csv(output_dir / "charging_schedule.csv", index=False)

    # 2. assignments.csv (combined)
    assignments_combined = pd.concat([baseline_plan.assignments, optimized_plan.assignments], ignore_index=True)
    assignments_combined.to_csv(output_dir / "assignments.csv", index=False)

    # 3. soc_trajectory.csv (combined)
    soc_combined = pd.concat([baseline_plan.soc, optimized_plan.soc], ignore_index=True)
    soc_combined.to_csv(output_dir / "soc_trajectory.csv", index=False)

    # 4. site_load.csv (combined)
    site_combined = pd.concat([baseline_plan.site_load, optimized_plan.site_load], ignore_index=True)
    site_combined.to_csv(output_dir / "site_load.csv", index=False)

    # 5. kpis.json
    write_json(kpis, output_dir / "kpis.json")

    # 6. recommendations.json
    write_json(recommendations, output_dir / "recommendations.json")

    # 7. agent_log.jsonl
    write_jsonl(agent_logs, output_dir / "agent_log.jsonl")

    # 8. run_meta.json
    write_json(run_meta, output_dir / "run_meta.json")

    if copy_to_latest:
        latest_dir = output_dir.parent / "latest"
        latest_dir.mkdir(parents=True, exist_ok=True)
        for item in output_dir.glob("*"):
            if item.is_file():
                shutil.copy2(item, latest_dir / item.name)

    return output_dir
