"""Dashboard pages package exports."""

import importlib

overview = importlib.import_module("src.dashboard.pages.01_overview")
charging_schedule = importlib.import_module("src.dashboard.pages.02_charging_schedule")
trip_allocation = importlib.import_module("src.dashboard.pages.03_trip_allocation")
cost_analytics = importlib.import_module("src.dashboard.pages.04_cost_analytics")
recommendations = importlib.import_module("src.dashboard.pages.05_recommendations")
scenarios = importlib.import_module("src.dashboard.pages.06_scenarios")
