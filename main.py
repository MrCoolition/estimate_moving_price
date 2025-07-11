import json
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Dict, Any
from difflib import get_close_matches

from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel, Field, root_validator

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
    distance_miles: float | None = Field(None, gt=0)
    move_date: datetime | None = Field(
        None, description="Date of move (YYYY-MM-DD)"
    )

    @classmethod
    def __get_validators__(cls):
        yield cls._unpack_nested
        yield from super().__get_validators__()

    @classmethod
    def _unpack_nested(cls, values):
        """Allow distance and date to be provided inside the items object."""
        if isinstance(values, list):
            if len(values) == 1 and isinstance(values[0], dict):
                values = values[0]
            else:
                raise ValueError("Request body must be a JSON object")
        if not isinstance(values, dict):
            raise ValueError("Request body must be a JSON object")

        # items may arrive in several forms depending on the calling tool
        items = values.get("items")
        if items is None:
            raise ValueError("items field is required")

        # JSON-encoded string
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except json.JSONDecodeError:
                raise ValueError("items must be a JSON object or list")

        # list-based forms sent by some calling tools
        if isinstance(items, list):
            # allow [[name, qty], ...] pairs in addition to dictionaries
            if all(isinstance(entry, list) and len(entry) == 2 for entry in items):
                try:
                    items = {str(name): int(qty) for name, qty in items}
                except (TypeError, ValueError):
                    raise ValueError("items list pairs must contain name and integer quantity")
            else:
                converted: Dict[str, int] = {}
                for entry in items:
                    if not isinstance(entry, dict):
                        raise ValueError("items list must contain objects")
                    name = (
                        entry.get("items")
                        or entry.get("item")
                        or entry.get("name")
                        or entry.get("id")
                    )
                    qty = (
                        entry.get("Qty")
                        or entry.get("qty")
                        or entry.get("quantity")
                        or entry.get("q")
                    )
                    if name is None or qty is None:
                        raise ValueError("items list entries must have name and quantity")
                    try:
                        converted[str(name)] = int(qty)
                    except (TypeError, ValueError):
                        raise ValueError("quantity values must be integers")
                items = converted
        elif not isinstance(items, dict):
            raise ValueError("items must be a JSON object or list")

        values["items"] = items

        if isinstance(items, dict):
            if "distance_miles" in items and values.get("distance_miles") is None:
                values["distance_miles"] = items.pop("distance_miles")
            if "move_date" in items and values.get("move_date") is None:
                values["move_date"] = items.pop("move_date")
        return values

    @root_validator(skip_on_failure=True)
    def _require_fields(cls, values):
        if values.get("distance_miles") is None:
            raise ValueError("distance_miles is required")
        if values.get("move_date") is None:
            raise ValueError("move_date is required")
        return values

class EstimateBreakdown(BaseModel):
    labor: float
    protective: float
    hours: float
    movers: int
    trucks: int
    weight: float
    volume: float


class EstimateResponse(BaseModel):
    cost: float
    breakdown: EstimateBreakdown


def _resolve_items(items: Dict[str, int]):
    weight = 0.0
    volume = 0.0
    unknown = []
    for name, qty in items.items():
        if not isinstance(qty, int) or qty <= 0:
            raise HTTPException(status_code=400, detail="Quantity must be positive")
        key = name.lower()
        info = ITEM_LOOKUP.get(key)
        if info is None:
            # attempt fuzzy match on known item keys
            choices = ITEM_LOOKUP.keys() if hasattr(ITEM_LOOKUP, "keys") else ITEM_LOOKUP
            match = get_close_matches(key, choices, n=1, cutoff=0.8)
            if match:
                try:
                    info = ITEM_LOOKUP[match[0]]
                except Exception:
                    info = None
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

    breakdown = EstimateBreakdown(
        labor=round(labor, 2),
        protective=round(protective, 2),
        hours=round(hours, 2),
        movers=movers,
        trucks=trucks,
        weight=weight,
        volume=volume,
    )
    return EstimateResponse(cost=round(cost, 2), breakdown=breakdown)


@api.post("/estimate", response_model=EstimateResponse)
def estimate(req: Any = Body(...)):
    """POST endpoint accepting flexible request formats."""
    if isinstance(req, list):
        if len(req) == 1 and isinstance(req[0], dict):
            req = req[0]
        else:
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    if not isinstance(req, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    try:
        model = EstimateRequest(**req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _calculate_estimate(model)


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


@api.get("/", include_in_schema=False)
async def root():
    """Health check endpoint used by hosting providers."""
    return {"status": "ok"}


@api.get("/info")
def info():
    """Basic usage information for browsers."""
    return {
        "message": "Moving price estimation service. POST or GET /estimate.",
    }

