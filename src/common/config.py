"""Typed configuration classes and YAML loader with dict override support."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional
import yaml


@dataclass
class HorizonConfig:
    plan_date: str = "auto"
    start_time: str = "18:00"
    slot_minutes: int = 15
    n_slots: int = 96

    @property
    def slot_hours(self) -> float:
        return self.slot_minutes / 60.0


@dataclass
class FleetConfig:
    n_vehicles: int = 20
    n_depots: int = 1
    vehicle_mix: dict[str, float] = field(default_factory=lambda: {"van_small": 0.4, "van_large": 0.4, "truck": 0.2})


@dataclass
class ChargerTypeConfig:
    count: int = 6
    power_kw: float = 11.0
    efficiency: float = 0.92
    derate_factor: float = 1.0


@dataclass
class ChargersConfig:
    ac: ChargerTypeConfig = field(default_factory=lambda: ChargerTypeConfig(count=6, power_kw=11.0, efficiency=0.92, derate_factor=1.0))
    dc: ChargerTypeConfig = field(default_factory=lambda: ChargerTypeConfig(count=2, power_kw=30.0, efficiency=0.94, derate_factor=0.85))


@dataclass
class SiteConfig:
    limit_kw: float = 90.0


@dataclass
class BatteryConfig:
    reserve_soc_pct: float = 15.0
    daily_ceiling_pct: float = 90.0
    high_soc_threshold_pct: float = 80.0
    end_of_horizon_target_pct: float = 60.0


@dataclass
class TripsConfig:
    safety_margin: float = 0.10
    turnaround_slots: int = 2


@dataclass
class TariffBandConfig:
    hours: str = "22:00-06:00"
    price: float = 4.5


@dataclass
class TariffConfig:
    offpeak: TariffBandConfig = field(default_factory=lambda: TariffBandConfig(hours="22:00-06:00", price=4.5))
    shoulder: TariffBandConfig = field(default_factory=lambda: TariffBandConfig(hours="06:00-18:00", price=7.0))
    peak: TariffBandConfig = field(default_factory=lambda: TariffBandConfig(hours="18:00-22:00", price=10.5))
    demand_charge_per_kw: float = 150.0
    noise_pct: float = 3.0


@dataclass
class CostsConfig:
    wear_cost_per_kwh_throughput: float = 0.8
    wear_cost_per_kwh_slot_above_threshold: float = 0.02
    unserved_trip_penalty: dict[int, float] = field(default_factory=lambda: {1: 100000.0, 2: 30000.0, 3: 8000.0})
    end_target_shortfall_penalty_per_kwh: float = 5.0


@dataclass
class WeightsConfig:
    unavailability: float = 1.0
    battery_wear: float = 1.0
    violations: float = 1.0


@dataclass
class SolverConfig:
    name: str = "cbc"
    time_limit_s: int = 60
    mip_gap: float = 0.01
    threads: int = 4


@dataclass
class ExternalConfig:
    use_real_weather: bool = False
    use_real_routing: bool = False
    use_real_tariff: bool = False


@dataclass
class LLMConfig:
    enabled: bool = False
    provider: str = "gemini"
    model: str = "gemini-2.0-flash"


@dataclass
class Config:
    random_seed: int = 42
    currency: str = "INR"
    horizon: HorizonConfig = field(default_factory=HorizonConfig)
    fleet: FleetConfig = field(default_factory=FleetConfig)
    chargers: ChargersConfig = field(default_factory=ChargersConfig)
    site: SiteConfig = field(default_factory=SiteConfig)
    battery: BatteryConfig = field(default_factory=BatteryConfig)
    trips: TripsConfig = field(default_factory=TripsConfig)
    tariff: TariffConfig = field(default_factory=TariffConfig)
    costs: CostsConfig = field(default_factory=CostsConfig)
    weights: WeightsConfig = field(default_factory=WeightsConfig)
    solver: SolverConfig = field(default_factory=SolverConfig)
    external: ExternalConfig = field(default_factory=ExternalConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    def to_dict(self) -> dict[str, Any]:
        """Converts config to nested dictionary."""
        return asdict(self)


def _deep_update(base: dict, updates: dict) -> dict:
    """Recursively updates nested dictionary."""
    for k, v in updates.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            base[k] = _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def load_config(config_path: Optional[str | Path] = None, overrides: Optional[dict[str, Any]] = None) -> Config:
    """Loads settings.yaml, parses into typed dataclass tree, and applies any runtime overrides."""
    if config_path is None:
        config_path = Path("config/settings.yaml")
    else:
        config_path = Path(config_path)

    raw: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    if overrides:
        raw = _deep_update(raw, overrides)

    horizon_raw = raw.get("horizon", {})
    horizon_cfg = HorizonConfig(
        plan_date=horizon_raw.get("plan_date", "auto"),
        start_time=horizon_raw.get("start_time", "18:00"),
        slot_minutes=int(horizon_raw.get("slot_minutes", 15)),
        n_slots=int(horizon_raw.get("n_slots", 96)),
    )

    fleet_raw = raw.get("fleet", {})
    fleet_cfg = FleetConfig(
        n_vehicles=int(fleet_raw.get("n_vehicles", 20)),
        n_depots=int(fleet_raw.get("n_depots", 1)),
        vehicle_mix=fleet_raw.get("vehicle_mix", {"van_small": 0.4, "van_large": 0.4, "truck": 0.2}),
    )

    chargers_raw = raw.get("chargers", {})
    ac_raw = chargers_raw.get("ac", {})
    dc_raw = chargers_raw.get("dc", {})
    chargers_cfg = ChargersConfig(
        ac=ChargerTypeConfig(
            count=int(ac_raw.get("count", 6)),
            power_kw=float(ac_raw.get("power_kw", 11.0)),
            efficiency=float(ac_raw.get("efficiency", 0.92)),
            derate_factor=float(ac_raw.get("derate_factor", 1.0)),
        ),
        dc=ChargerTypeConfig(
            count=int(dc_raw.get("count", 2)),
            power_kw=float(dc_raw.get("power_kw", 30.0)),
            efficiency=float(dc_raw.get("efficiency", 0.94)),
            derate_factor=float(dc_raw.get("derate_factor", 0.85)),
        ),
    )

    site_raw = raw.get("site", {})
    site_cfg = SiteConfig(limit_kw=float(site_raw.get("limit_kw", 90.0)))

    battery_raw = raw.get("battery", {})
    battery_cfg = BatteryConfig(
        reserve_soc_pct=float(battery_raw.get("reserve_soc_pct", 15.0)),
        daily_ceiling_pct=float(battery_raw.get("daily_ceiling_pct", 90.0)),
        high_soc_threshold_pct=float(battery_raw.get("high_soc_threshold_pct", 80.0)),
        end_of_horizon_target_pct=float(battery_raw.get("end_of_horizon_target_pct", 60.0)),
    )

    trips_raw = raw.get("trips", {})
    trips_cfg = TripsConfig(
        safety_margin=float(trips_raw.get("safety_margin", 0.10)),
        turnaround_slots=int(trips_raw.get("turnaround_slots", 2)),
    )

    tariff_raw = raw.get("tariff", {})
    offpeak_raw = tariff_raw.get("offpeak", {})
    shoulder_raw = tariff_raw.get("shoulder", {})
    peak_raw = tariff_raw.get("peak", {})
    tariff_cfg = TariffConfig(
        offpeak=TariffBandConfig(hours=offpeak_raw.get("hours", "22:00-06:00"), price=float(offpeak_raw.get("price", 4.5))),
        shoulder=TariffBandConfig(hours=shoulder_raw.get("hours", "06:00-18:00"), price=float(shoulder_raw.get("price", 7.0))),
        peak=TariffBandConfig(hours=peak_raw.get("hours", "18:00-22:00"), price=float(peak_raw.get("price", 10.5))),
        demand_charge_per_kw=float(tariff_raw.get("demand_charge_per_kw", 150.0)),
        noise_pct=float(tariff_raw.get("noise_pct", 3.0)),
    )

    costs_raw = raw.get("costs", {})
    unserved_raw = costs_raw.get("unserved_trip_penalty", {1: 100000.0, 2: 30000.0, 3: 8000.0})
    unserved_map = {int(k): float(v) for k, v in unserved_raw.items()}
    costs_cfg = CostsConfig(
        wear_cost_per_kwh_throughput=float(costs_raw.get("wear_cost_per_kwh_throughput", 0.8)),
        wear_cost_per_kwh_slot_above_threshold=float(costs_raw.get("wear_cost_per_kwh_slot_above_threshold", 0.02)),
        unserved_trip_penalty=unserved_map,
        end_target_shortfall_penalty_per_kwh=float(costs_raw.get("end_target_shortfall_penalty_per_kwh", 5.0)),
    )

    weights_raw = raw.get("weights", {})
    weights_cfg = WeightsConfig(
        unavailability=float(weights_raw.get("unavailability", 1.0)),
        battery_wear=float(weights_raw.get("battery_wear", 1.0)),
        violations=float(weights_raw.get("violations", 1.0)),
    )

    solver_raw = raw.get("solver", {})
    solver_cfg = SolverConfig(
        name=solver_raw.get("name", "cbc"),
        time_limit_s=int(solver_raw.get("time_limit_s", 60)),
        mip_gap=float(solver_raw.get("mip_gap", 0.01)),
        threads=int(solver_raw.get("threads", 4)),
    )

    ext_raw = raw.get("external", {})
    ext_cfg = ExternalConfig(
        use_real_weather=bool(ext_raw.get("use_real_weather", False)),
        use_real_routing=bool(ext_raw.get("use_real_routing", False)),
        use_real_tariff=bool(ext_raw.get("use_real_tariff", False)),
    )

    llm_raw = raw.get("llm", {})
    llm_cfg = LLMConfig(
        enabled=bool(llm_raw.get("enabled", False)),
        provider=llm_raw.get("provider", "gemini"),
        model=llm_raw.get("model", "gemini-2.0-flash"),
    )

    return Config(
        random_seed=int(raw.get("random_seed", 42)),
        currency=str(raw.get("currency", "INR")),
        horizon=horizon_cfg,
        fleet=fleet_cfg,
        chargers=chargers_cfg,
        site=site_cfg,
        battery=battery_cfg,
        trips=trips_cfg,
        tariff=tariff_cfg,
        costs=costs_cfg,
        weights=weights_cfg,
        solver=solver_cfg,
        external=ext_cfg,
        llm=llm_cfg,
    )
