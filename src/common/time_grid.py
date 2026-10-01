"""Time grid utilities for converting discrete slots to timestamps and back."""

from __future__ import annotations

import datetime
from typing import Union
import pandas as pd


def get_plan_start_datetime(plan_date: Union[str, datetime.date], start_time: str = "18:00") -> datetime.datetime:
    """Computes the plan starting datetime.
    
    If plan_date is 'auto', uses tomorrow's date.
    """
    if plan_date == "auto" or not plan_date:
        base_date = datetime.date.today() + datetime.timedelta(days=1)
    elif isinstance(plan_date, str):
        base_date = datetime.datetime.strptime(plan_date, "%Y-%m-%d").date()
    elif isinstance(plan_date, datetime.date):
        base_date = plan_date
    else:
        base_date = datetime.date.today() + datetime.timedelta(days=1)

    hour, minute = map(int, start_time.split(":"))
    return datetime.datetime.combine(base_date, datetime.time(hour=hour, minute=minute))


def slot_to_datetime(slot: int, start_dt: datetime.datetime, slot_minutes: int = 15) -> datetime.datetime:
    """Returns the starting datetime of a given slot."""
    return start_dt + datetime.timedelta(minutes=int(slot) * slot_minutes)


def datetime_to_slot(dt: datetime.datetime, start_dt: datetime.datetime, slot_minutes: int = 15) -> int:
    """Converts a datetime into the nearest slot index relative to start_dt."""
    diff_minutes = (dt - start_dt).total_seconds() / 60.0
    return int(round(diff_minutes / slot_minutes))


def slot_to_timestamp_str(slot: int, start_dt: datetime.datetime, slot_minutes: int = 15) -> str:
    """Converts slot index to standard ISO string YYYY-MM-DD HH:MM."""
    dt = slot_to_datetime(slot, start_dt, slot_minutes)
    return dt.strftime("%Y-%m-%d %H:%M")


def time_str_to_slot(time_str: str, start_dt: datetime.datetime, slot_minutes: int = 15) -> int:
    """Maps a 24h HH:MM time string to the closest forward slot index within horizon.
    
    If the time is earlier than the plan start hour, it is assumed to fall on the next calendar day.
    """
    target_hour, target_min = map(int, time_str.split(":"))
    target_time = datetime.time(target_hour, target_min)
    
    # Try current day
    dt_day0 = datetime.datetime.combine(start_dt.date(), target_time)
    if dt_day0 >= start_dt:
        return datetime_to_slot(dt_day0, start_dt, slot_minutes)
    
    # Next day
    dt_day1 = datetime.datetime.combine(start_dt.date() + datetime.timedelta(days=1), target_time)
    return datetime_to_slot(dt_day1, start_dt, slot_minutes)


def parse_time_window_slots(window_str: str, start_dt: datetime.datetime, slot_minutes: int = 15, n_slots: int = 96) -> list[int]:
    """Parses window like '22:00-06:00' and returns the list of matching slot indices within 0..n_slots-1."""
    start_str, end_str = window_str.split("-")
    start_h, start_m = map(int, start_str.split(":"))
    end_h, end_m = map(int, end_str.split(":"))
    
    matching_slots = []
    for s in range(n_slots):
        dt = slot_to_datetime(s, start_dt, slot_minutes)
        cur_min = dt.hour * 60 + dt.minute
        win_start_min = start_h * 60 + start_m
        win_end_min = end_h * 60 + end_m
        
        if win_start_min < win_end_min:
            if win_start_min <= cur_min < win_end_min:
                matching_slots.append(s)
        else:
            # Wraps midnight (e.g. 22:00 to 06:00)
            if cur_min >= win_start_min or cur_min < win_end_min:
                matching_slots.append(s)
    return matching_slots


def build_time_grid_dataframe(start_dt: datetime.datetime, n_slots: int = 96, slot_minutes: int = 15) -> pd.DataFrame:
    """Constructs the reference time grid DataFrame with slots, timestamps, and hours."""
    rows = []
    for s in range(n_slots):
        dt = slot_to_datetime(s, start_dt, slot_minutes)
        rows.append({
            "slot": s,
            "timestamp": dt.strftime("%Y-%m-%d %H:%M"),
            "datetime": dt,
            "hour": dt.hour,
            "minute": dt.minute,
        })
    return pd.DataFrame(rows)
