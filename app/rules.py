from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class AccessRule:
    code: str
    lbs_per_mover_hour: float
    description: str


@dataclass(frozen=True)
class RateCard:
    mover_rate_per_hour: float
    truck_rate_per_hour: float


@dataclass
class MovingRules:
    access_rules: Dict[str, AccessRule]
    truck_capacity_lbs: int
    baseline_mover_threshold_lbs: int
    additional_mover_step_lbs: int
    min_movers: int
    max_movers: int
    max_trucks: int
    travel_charge_hours: float
    min_billable_hours: float
    local_extra_minutes_under_30_miles: int
    stairs_minutes_per_flight: int
    long_carry_minutes_per_50ft: int
    elevator_minutes: int
    mileage_rate: float
    base_fee: float
    nte_buffer_percent: float
    rate_cards: Dict[str, Dict[str, RateCard]]

    def access_for_location(self, location: Dict[str, Any]) -> AccessRule:
        location_type = (location.get("location_type") or "").lower()
        floor = int(location.get("floor") or 1)
        stairs = int(location.get("stairs_flights") or 0)
        elevator = bool(location.get("elevator"))
        if location_type in {"dock"}:
            return self.access_rules["1E"]
        if location_type in {"storage"}:
            return self.access_rules["1D"]
        if location_type in {"house", "townhouse"}:
            if stairs > 0:
                return self.access_rules["1A"]
            return self.access_rules["1C"]
        if location_type in {"apartment", "condo"}:
            if floor > 1 and not elevator:
                return self.access_rules["1B"]
            if floor > 1 and elevator:
                return self.access_rules["1A"]
            return self.access_rules["1C"]
        # default fallback to first-floor rule
        return self.access_rules["1C"]

    def rate_card_for(self, move_date: date, *, is_local: bool) -> RateCard:
        weekday = move_date.weekday()
        weekend = weekday >= 5
        move_type = "localMoves" if is_local else "intrastateMoves"
        rate_group = "ratesFridayToSaturday" if weekend else "ratesMondayToThursday"
        return self.rate_cards[move_type][rate_group]


def load_rules(path: str | Path) -> MovingRules:
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    ctx = payload["movingQuoterContext"]
    access_rules = {
        sub["subrule"]: AccessRule(
            code=sub["subrule"],
            lbs_per_mover_hour=float(sub["rateOfMovement"]),
            description=sub.get("condition", ""),
        )
        for sub in ctx["rules"][0]["subrules"]
    }
    rate_cards: Dict[str, Dict[str, RateCard]] = {}
    for move_type, move_data in ctx["pricing"].items():
        rate_cards[move_type] = {}
        for key, rates in move_data.items():
            if not key.startswith("rates"):
                continue
            rate_cards[move_type][key] = RateCard(
                mover_rate_per_hour=float(rates["moverRatePerHour"]),
                truck_rate_per_hour=float(rates["truckRatePerHour"]),
            )
    return MovingRules(
        access_rules=access_rules,
        truck_capacity_lbs=8000,
        baseline_mover_threshold_lbs=4000,
        additional_mover_step_lbs=2500,
        min_movers=2,
        max_movers=6,
        max_trucks=3,
        travel_charge_hours=1.0,
        min_billable_hours=3.0,
        local_extra_minutes_under_30_miles=20,
        stairs_minutes_per_flight=6,
        long_carry_minutes_per_50ft=5,
        elevator_minutes=10,
        mileage_rate=2.25,
        base_fee=45.0,
        nte_buffer_percent=0.15,
        rate_cards=rate_cards,
    )
