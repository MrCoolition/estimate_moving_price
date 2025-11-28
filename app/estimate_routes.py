from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .furniture_catalog import FurnitureCatalog
from .quotes import LocationProfile, MoveSpec, compute_quote

BASE_DIR = Path(__file__).resolve().parent.parent
CATALOG_PATH = BASE_DIR / "data" / "estimation_weights_volumes_categories.json"

furniture_catalog = FurnitureCatalog(CATALOG_PATH)
router = APIRouter()


class EstimateRequest(BaseModel):
    distance_miles: float = Field(..., ge=0)
    move_date: date
    items: Dict[str, int]

    @field_validator("items")
    @classmethod
    def validate_items(cls, value: Dict[str, int]) -> Dict[str, int]:
        if not isinstance(value, dict) or not value:
            raise ValueError("items must be a non-empty object")
        normalized: Dict[str, int] = {}
        for key, qty in value.items():
            try:
                quantity = int(qty)
            except (TypeError, ValueError):
                raise ValueError("item quantities must be integers")
            if quantity < 0:
                raise ValueError("item quantities must be non-negative")
            normalized[str(key)] = quantity
        return normalized


@router.post("/estimate")
async def create_estimate(payload: EstimateRequest):
    try:
        total_weight_lbs, breakdown = furniture_catalog.total_weight(payload.items)
    except ValueError as exc:  # pragma: no cover - handled by FastAPI validation
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    is_intrastate = payload.distance_miles > 30
    origin_to_destination_minutes = max(20.0, float(payload.distance_miles) * 1.5)
    friday_or_saturday = payload.move_date.weekday() in (4, 5)

    spec = MoveSpec(
        total_weight_lbs=total_weight_lbs,
        location_profile=LocationProfile.MULTI_FLOOR,
        friday_or_saturday=friday_or_saturday,
        is_intrastate=is_intrastate,
        origin_to_destination_minutes=origin_to_destination_minutes,
        distance_miles=float(payload.distance_miles),
    )

    quote = compute_quote(spec)
    quote["inventory_breakdown"] = breakdown
    quote["total_weight_lbs"] = total_weight_lbs
    return quote
