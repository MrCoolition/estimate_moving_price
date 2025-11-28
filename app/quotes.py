from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict

from .pricing import LocationContext, compute_productivity_hours, protective_materials_charge
from .rules import MovingRules, load_rules

BASE_DIR = Path(__file__).resolve().parent.parent
RULES_PATH = BASE_DIR / "data" / "moving_rules.json"


class LocationProfile(str, Enum):
    EASY = "easy"
    MULTI_FLOOR = "multi_floor"

    def access_code(self) -> str:
        if self is LocationProfile.MULTI_FLOOR:
            return "1A"
        return "1C"

    def default_location(self) -> Dict[str, int | float | bool | str]:
        if self is LocationProfile.MULTI_FLOOR:
            return {"location_type": "apartment", "floor": 2, "stairs_flights": 1}
        return {"location_type": "house", "floor": 1, "stairs_flights": 0}


@dataclass
class MoveSpec:
    total_weight_lbs: float
    location_profile: LocationProfile
    friday_or_saturday: bool
    is_intrastate: bool
    origin_to_destination_minutes: float
    distance_miles: float


def _load_rules() -> MovingRules:
    return load_rules(RULES_PATH)


def _build_location(profile: LocationProfile, rules: MovingRules) -> LocationContext:
    access = rules.access_rules.get(profile.access_code())
    if not access:
        access = next(iter(rules.access_rules.values()))
    return LocationContext(raw=profile.default_location(), access_rule=access)


def compute_quote(spec: MoveSpec) -> Dict[str, object]:
    rules = _load_rules()
    origin_ctx = _build_location(spec.location_profile, rules)
    destination_ctx = _build_location(spec.location_profile, rules)
    move_type = "intrastateMoves" if spec.is_intrastate else "localMoves"
    rate_group = "ratesFridayToSaturday" if spec.friday_or_saturday else "ratesMondayToThursday"
    rate_card = rules.rate_cards[move_type][rate_group]
    movers = rules.min_movers
    if spec.total_weight_lbs > rules.baseline_mover_threshold_lbs:
        extra = spec.total_weight_lbs - rules.baseline_mover_threshold_lbs
        movers += math.ceil(extra / rules.additional_mover_step_lbs)
    movers = max(rules.min_movers, min(movers, rules.max_movers))
    trucks = max(1, math.ceil(spec.total_weight_lbs / rules.truck_capacity_lbs))
    work_hours = compute_productivity_hours(spec.total_weight_lbs, movers, origin_ctx, destination_ctx)
    travel_hours = spec.origin_to_destination_minutes / 60.0
    total_hours = work_hours + travel_hours
    billable_hours = max(total_hours, rules.min_billable_hours)
    mover_rate = float(rate_card.mover_rate_per_hour)
    truck_rate = float(rate_card.truck_rate_per_hour)
    labor_cost = billable_hours * (mover_rate * movers + truck_rate * trucks)
    mileage_cost = spec.distance_miles * rules.mileage_rate
    protective_charge = protective_materials_charge(spec.total_weight_lbs)
    base_fee = rules.base_fee
    total_price = labor_cost + mileage_cost + protective_charge + base_fee
    return {
        "total_price": round(total_price, 2),
        "currency": "USD",
        "labor_cost": round(labor_cost, 2),
        "mileage_cost": round(mileage_cost, 2),
        "protective_materials": round(protective_charge, 2),
        "base_fee": round(base_fee, 2),
        "billable_hours": round(billable_hours, 2),
        "work_hours": round(work_hours, 2),
        "travel_hours": round(travel_hours, 2),
        "movers": movers,
        "trucks": trucks,
    }
