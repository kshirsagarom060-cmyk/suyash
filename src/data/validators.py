"""Table validation for input data integrity."""

from __future__ import annotations

from typing import Any
import pandas as pd


class DataValidationError(Exception):
    """Raised when fatal input table integrity issues are encountered."""
    pass


def validate_tables(tables: dict[str, pd.DataFrame], config: Any, allow_impossible_trips: bool = False) -> list[str]:
    """Validates input tables against schema invariants.
    
    Raises DataValidationError on fatal integrity flaws. Returns a list of non-fatal warning messages.
    """
    warnings: list[str] = []
    
    # Check table existence
    required = ["depots", "chargers", "vehicles", "trips", "tariffs", "weather"]
    for req in required:
        if req not in tables or tables[req].empty:
            raise DataValidationError(f"Missing or empty required table: '{req}'")

    depots = tables["depots"]
    chargers = tables["chargers"]
    vehicles = tables["vehicles"]
    trips = tables["trips"]
    tariffs = tables["tariffs"]
    weather = tables["weather"]

    # 1. Duplicate IDs
    if depots["depot_id"].duplicated().any():
        raise DataValidationError("Duplicate depot_id found in depots table.")
    if chargers["charger_id"].duplicated().any():
        raise DataValidationError("Duplicate charger_id found in chargers table.")
    if vehicles["vehicle_id"].duplicated().any():
        raise DataValidationError("Duplicate vehicle_id found in vehicles table.")
    if trips["trip_id"].duplicated().any():
        raise DataValidationError("Duplicate trip_id found in trips table.")

    # 2. Depot References
    depot_ids = set(depots["depot_id"])
    for d_id in chargers["depot_id"]:
        if d_id not in depot_ids:
            raise DataValidationError(f"Charger references unknown depot_id: {d_id}")
    for d_id in vehicles["depot_id"]:
        if d_id not in depot_ids:
            raise DataValidationError(f"Vehicle references unknown depot_id: {d_id}")
    for d_id in trips["depot_id"]:
        if d_id not in depot_ids:
            raise DataValidationError(f"Trip references unknown depot_id: {d_id}")

    # 3. Vehicle constraints
    if (vehicles["battery_capacity_kwh"] <= 0).any():
        raise DataValidationError("Vehicles must have positive battery_capacity_kwh.")
    if (vehicles["current_soc_pct"] < 0).any() or (vehicles["current_soc_pct"] > 100).any():
        raise DataValidationError("Vehicle current_soc_pct must be within [0, 100].")
    if (vehicles["reserve_soc_pct"] >= vehicles["ceiling_soc_pct"]).any():
        raise DataValidationError("Vehicle reserve_soc_pct must be strictly less than ceiling_soc_pct.")
    if (vehicles["soh"] <= 0).any() or (vehicles["soh"] > 1.0).any():
        raise DataValidationError("Vehicle SOH must be in (0, 1.0].")

    # 4. Trip constraints
    if (trips["departure_slot"] >= trips["return_slot"]).any():
        raise DataValidationError("Trip departure_slot must be strictly less than return_slot.")
    if (trips["departure_slot"] < 0).any() or (trips["return_slot"] > config.horizon.n_slots).any():
        raise DataValidationError(f"Trip slots must be within [0, {config.horizon.n_slots}].")
    if (trips["distance_km"] <= 0).any():
        raise DataValidationError("Trip distance_km must be positive.")
    if (trips["payload_kg"] < 0).any():
        raise DataValidationError("Trip payload_kg cannot be negative.")

    max_vehicle_payload = vehicles["payload_capacity_kg"].max()
    if (trips["payload_kg"] > max_vehicle_payload).any():
        raise DataValidationError(
            f"Trip payload exceeds max fleet payload capacity ({max_vehicle_payload} kg)."
        )

    # 5. Tariff constraints
    n_slots = config.horizon.n_slots
    for d_id in depot_ids:
        depot_tariffs = tariffs[tariffs["depot_id"] == d_id]
        if len(depot_tariffs) != n_slots:
            raise DataValidationError(
                f"Tariff table has {len(depot_tariffs)} rows for depot {d_id}, expected {n_slots} slots."
            )
        slots_present = set(depot_tariffs["slot"])
        if slots_present != set(range(n_slots)):
            raise DataValidationError(f"Tariff table for depot {d_id} has missing slot indices.")
        if (depot_tariffs["price_per_kwh"] < 0).any():
            raise DataValidationError("Negative electricity price found in tariff table.")

    # 6. Weather table
    if len(weather) != n_slots:
        raise DataValidationError(f"Weather table has {len(weather)} rows, expected {n_slots} slots.")

    # 7. Base feasibility check (at least one vehicle fits each trip)
    usable_vehicles = vehicles[vehicles["status"] == "available"]
    for _, trip in trips.iterrows():
        fits = False
        for _, veh in usable_vehicles.iterrows():
            if veh["depot_id"] != trip["depot_id"]:
                continue
            if trip["payload_kg"] > veh["payload_capacity_kg"]:
                continue
            usable_kwh = veh["battery_capacity_kwh"] * veh["soh"]
            e_max = (veh["ceiling_soc_pct"] / 100.0) * usable_kwh
            e_min = (veh["reserve_soc_pct"] / 100.0) * usable_kwh
            # basic energy estimate
            est_energy = trip["distance_km"] * veh["efficiency_kwh_per_km"] * (1.0 + config.trips.safety_margin)
            if est_energy + e_min <= e_max:
                fits = True
                break
        if not fits:
            msg = f"Trip {trip['trip_id']} (dist {trip['distance_km']}km, payload {trip['payload_kg']}kg) has no feasible vehicle."
            if allow_impossible_trips:
                warnings.append(msg)
            else:
                raise DataValidationError(msg)

    return warnings
