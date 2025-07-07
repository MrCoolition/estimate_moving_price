import json
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DATA_DIR = Path(__file__).parent / "data"

# load item weights/volumes
with open(DATA_DIR / "estimation_weights_volumes_categories.json", "r") as f:
    ITEMS = json.load(f)

# build alias -> weight/volume mapping
ITEM_LOOKUP: Dict[str, Dict[str, float]] = {}
for item in ITEMS:
    info = {"weight": item["weight_lbs"], "volume": item["volume_cuft"]}
    for alias in item.get("aliases", []):
        ITEM_LOOKUP[alias.lower()] = info
    ITEM_LOOKUP[item["id"].lower()] = info
    ITEM_LOOKUP[item["name"].lower()] = info

# load pricing rules
with open(DATA_DIR / "moving_rules.json", "r") as f:
    RULES = json.load(f)["movingQuoterContext"]

api = FastAPI(title="Moving Price Estimator")


class EstimateRequest(BaseModel):
    items: Dict[str, int] = Field(
        ...,
        description="Mapping of item names/ids to quantities"
    )
    distance_miles: float = Field(..., gt=0)
    move_date: datetime = Field(..., description="Date of move (YYYY-MM-DD)")


class EstimateResponse(BaseModel):
    cost: float
    breakdown: Dict[str, float]


def _resolve_items(items: Dict[str, int]):
    weight = 0.0
    volume = 0.0
    unknown = []
    for name, qty in items.items():
        info = ITEM_LOOKUP.get(name.lower())
        if info is None:
            unknown.append(name)
            continue
        weight += info["weight"] * qty
        volume += info["volume"] * qty
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown items: {', '.join(unknown)}")
    return weight, volume


def _get_rates(distance: float, move_date: datetime):
    weekday = move_date.weekday()
    weekend = weekday >= 4  # Friday=4, Saturday=5
    move_type = "localMoves" if distance <= 30 else "intrastateMoves"
    pricing = RULES["pricing"][move_type]
    if weekend:
        rates = pricing["ratesFridayToSaturday"]
    else:
        rates = pricing["ratesMondayToThursday"]
    return rates["moverRatePerHour"], rates["truckRatePerHour"]


def _num_movers(weight: float) -> int:
    if weight <= 4000:
        return 2
    extra = max(weight - 4000, 0)
    return 2 + ceil(extra / 2500)


def _num_trucks(weight: float) -> int:
    return ceil(weight / 8000)


def _estimate_hours(weight: float, movers: int, distance: float) -> float:
    base_rate_per_mover = 310.0
    hours = weight / (base_rate_per_mover * movers)
    hours += 1.0  # travel charge
    if distance <= 30:
        hours += 20 / 60
    else:
        # approximate actual drive time plus warehouse trips
        drive = max(distance / 50, 0.5)
        hours += drive + 1.0  # 0.5 each way warehouse
    return max(hours, 3.0)


def _calculate_estimate(req: EstimateRequest):
    weight, volume = _resolve_items(req.items)
    movers = _num_movers(weight)
    trucks = _num_trucks(weight)
    mover_rate, truck_rate = _get_rates(req.distance_miles, req.move_date)
    hours = _estimate_hours(weight, movers, req.distance_miles)

    labor = (mover_rate * movers + truck_rate * trucks) * hours
    protective = 5.0 * ceil(weight / 1000)
    cost = labor + protective
    return {
        "cost": round(cost, 2),
        "breakdown": {
            "labor": round(labor, 2),
            "protective": round(protective, 2),
            "hours": round(hours, 2),
            "movers": movers,
            "trucks": trucks,
            "weight": weight,
            "volume": volume,
        },
    }


@api.post("/estimate", response_model=EstimateResponse)
def estimate(req: EstimateRequest):
    return _calculate_estimate(req)


@api.get("/estimate", response_model=EstimateResponse)
def estimate_get(items: str, distance_miles: float, move_date: datetime):
    """GET variant for simple testing via browser or curl.

    The ``items`` parameter should be a JSON mapping of item names/ids to
    quantities. Example: ``{"bed_king_mattress": 1, "bar_stool": 2}``.
    """
    try:
        items_dict = json.loads(items)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid items JSON")
    req = EstimateRequest(
        items=items_dict, distance_miles=distance_miles, move_date=move_date
    )
    return _calculate_estimate(req)


@api.get("/")
def root():
    return {
        "message": "Moving price estimation service. POST or GET /estimate.",
    }

