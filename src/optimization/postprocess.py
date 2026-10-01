"""Postprocessing utilities to convert solver outputs into standardized Plan structures and assign physical chargers."""

from __future__ import annotations

from typing import Any
import pandas as pd

from src.common.config import Config
from src.common.schemas import OptimizationInput, Plan
from src.common.time_grid import get_plan_start_datetime, slot_to_timestamp_str


def assign_physical_chargers(
    charging_df: pd.DataFrame,
    chargers_df: pd.DataFrame,
    n_slots: int,
) -> pd.DataFrame:
    """Assigns physical charger IDs using interval coloring algorithm per charger type."""
    if charging_df.empty:
        return charging_df

    res_df = charging_df.copy()
    res_df["charger_id"] = "UNASSIGNED"

    ac_chargers = chargers_df[chargers_df["type"] == "AC"]["charger_id"].tolist()
    dc_chargers = chargers_df[chargers_df["type"] == "DC"]["charger_id"].tolist()

    for ch_type, charger_pool in [("AC", ac_chargers), ("DC", dc_chargers)]:
        type_mask = res_df["charger_type"] == ch_type
        type_charges = res_df[type_mask]
        if type_charges.empty or not charger_pool:
            continue

        # Find continuous charging sessions per vehicle
        # session = (vehicle_id, start_slot, end_slot)
        sessions = []
        for v_id, v_group in type_charges.groupby("vehicle_id"):
            v_slots = sorted(v_group["slot"].tolist())
            if not v_slots:
                continue
            cur_start = v_slots[0]
            prev_slot = v_slots[0]
            for s in v_slots[1:]:
                if s == prev_slot + 1:
                    prev_slot = s
                else:
                    sessions.append((v_id, cur_start, prev_slot))
                    cur_start = s
                    prev_slot = s
            sessions.append((v_id, cur_start, prev_slot))

        # Sort sessions by start slot
        sessions.sort(key=lambda x: (x[1], x[2]))

        # Track occupied charger intervals: charger_id -> list of (start, end)
        occupied: dict[str, list[tuple[int, int]]] = {c: [] for c in charger_pool}

        for v_id, s_start, s_end in sessions:
            assigned_c = None
            for c_id in charger_pool:
                # check collision
                collision = False
                for occ_start, occ_end in occupied[c_id]:
                    if not (s_end < occ_start or s_start > occ_end):
                        collision = True
                        break
                if not collision:
                    assigned_c = c_id
                    break

            if assigned_c is None:
                assigned_c = charger_pool[0]  # Fallback if over-capacity

            occupied[assigned_c].append((s_start, s_end))

            # Assign to rows
            mask_sess = (
                (res_df["vehicle_id"] == v_id) &
                (res_df["charger_type"] == ch_type) &
                (res_df["slot"] >= s_start) &
                (res_df["slot"] <= s_end)
            )
            res_df.loc[mask_sess, "charger_id"] = assigned_c

    return res_df


def build_plan_dataframes(
    plan_name: str,
    opt_input: OptimizationInput,
    config: Config,
    charging_records: list[dict[str, Any]],
    assignment_records: list[dict[str, Any]],
    soc_records: list[dict[str, Any]],
    site_load_records: list[dict[str, Any]],
    solver_info: dict[str, Any],
) -> Plan:
    """Builds and packages Plan dataclass with consistent schemas."""
    start_dt = get_plan_start_datetime(config.horizon.plan_date, config.horizon.start_time)
    slot_minutes = config.horizon.slot_minutes

    charging_df = pd.DataFrame(charging_records)
    if not charging_df.empty:
        charging_df = assign_physical_chargers(charging_df, opt_input.chargers, opt_input.n_slots)
        if "timestamp" not in charging_df.columns:
            charging_df["timestamp"] = charging_df["slot"].apply(
                lambda s: slot_to_timestamp_str(int(s), start_dt, slot_minutes)
            )
        charging_df["plan"] = plan_name
    else:
        charging_df = pd.DataFrame(columns=[
            "vehicle_id", "charger_id", "charger_type", "slot", "timestamp",
            "power_kw_grid", "energy_to_battery_kwh", "price_per_kwh", "plan"
        ])

    assignments_df = pd.DataFrame(assignment_records)
    if not assignments_df.empty:
        assignments_df["plan"] = plan_name
    else:
        assignments_df = pd.DataFrame(columns=[
            "trip_id", "vehicle_id", "served", "required_energy_kwh", "departure_soc_pct", "plan"
        ])

    soc_df = pd.DataFrame(soc_records)
    if not soc_df.empty:
        if "timestamp" not in soc_df.columns:
            soc_df["timestamp"] = soc_df["slot"].apply(
                lambda s: slot_to_timestamp_str(int(s), start_dt, slot_minutes)
            )
        soc_df["plan"] = plan_name
    else:
        soc_df = pd.DataFrame(columns=[
            "vehicle_id", "slot", "timestamp", "soc_pct", "energy_kwh", "on_trip", "plan"
        ])

    site_df = pd.DataFrame(site_load_records)
    if not site_df.empty:
        if "timestamp" not in site_df.columns:
            site_df["timestamp"] = site_df["slot"].apply(
                lambda s: slot_to_timestamp_str(int(s), start_dt, slot_minutes)
            )
        site_df["plan"] = plan_name
    else:
        site_df = pd.DataFrame(columns=[
            "slot", "timestamp", "site_kw", "site_limit_kw", "price_per_kwh", "plan"
        ])

    return Plan(
        name=plan_name,
        charging=charging_df,
        assignments=assignments_df,
        soc=soc_df,
        site_load=site_df,
        solver_info=solver_info,
    )
