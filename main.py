# main.py
from __future__ import annotations
import os, json
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

# --- helpers ---------------------------------------------------------------

def _iso(d: Any) -> str:
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    s = str(d).strip().replace("/", "-")
    return datetime.fromisoformat(s).date().isoformat()

# fallback weights; you can expand from your catalog at any time
DEFAULT_WEIGHTS = {
    "king size bed": 250.0,
    "king size bed with box spring and headboard": 350.0,
    "box spring": 60.0,
    "headboard": 40.0,
    "regular refrigerator": 250.0,
    "refrigerator": 250.0,
    "dining room table with five chairs": 220.0,
    "dining table": 180.0,
    "chair": 25.0,
    "bar_stool": 15.0,
    "bed_king_mattress": 150.0,
    "box_small": 15.0,
    "box_medium": 30.0,
    "box_large": 45.0
}

def _norm(s: str) -> str:
    return " ".join(str(s).strip().lower().replace("_"," ").split())

# --- request schema (accepts both array-of-objects and mapping) ------------

class EstimateRequest(BaseModel):
    items: Dict[str, int] = Field(..., description="items mapping after normalization")
    distance_miles: float = Field(..., ge=0)
    move_date: str = Field(..., description="YYYY-MM-DD")
    idempotency_key: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object")

        # move_date & distance_miles
        dm = data.get("distance_miles")
        md = data.get("move_date")
        if dm is None or md is None:
            raise ValueError("distance_miles and move_date are required")
        if isinstance(dm, str):
            data["distance_miles"] = float(dm)
        data["move_date"] = _iso(md)

        raw_items = data.get("items")
        if raw_items is None:
            raise ValueError("items is required")

        # 1) array of objects [{items|item|name, Qty|qty|quantity|q}]
        if isinstance(raw_items, list):
            mapping: Dict[str, int] = {}
            for entry in raw_items:
                if not isinstance(entry, dict):
                    raise ValueError("items list must contain objects")
                name = entry.get("items") or entry.get("item") or entry.get("name")
                qty  = entry.get("Qty")   or entry.get("qty")  or entry.get("quantity") or entry.get("q")
                if name is None or qty is None:
                    raise ValueError("each item must have name/items and Qty/quantity")
                mapping[str(name)] = int(qty)
            data["items"] = mapping

        # 2) mapping {"slug": qty, ...}
        elif isinstance(raw_items, dict):
            mapping = {}
            for k, v in raw_items.items():
                mapping[str(k)] = int(v)
            data["items"] = mapping

        # 3) unacceptable
        else:
            raise ValueError("items must be an array of objects or a mapping")

        return data

# --- response DTOs ---------------------------------------------------------

class InventoryRow(BaseModel):
    item_id: str
    name: str
    category: str
    quantity: int
    weight_each_lbs: float
    weight_total_lbs: float

# --- app -------------------------------------------------------------------

api = FastAPI(title="Estimate Moving Price", version="2025-10-04")
_IDEMP: Dict[str, Dict[str, Any]] = {}

@api.get("/", include_in_schema=False)
def health():
    return {"status": "ok"}

def _resolve_items(items: Dict[str, int]) -> Tuple[List[InventoryRow], float]:
    rows: List[InventoryRow] = []
    total = 0.0
    for name, qty in items.items():
        if int(qty) <= 0:
            raise HTTPException(status_code=400, detail="Quantities must be positive integers")
        key = _norm(name)
        w_each = DEFAULT_WEIGHTS.get(key, 35.0)
        rows.append(InventoryRow(
            item_id=key.replace(" ", "_"),
            name=name,
            category="carton" if key.startswith("box_") else "misc",
            quantity=int(qty),
            weight_each_lbs=float(w_each),
            weight_total_lbs=float(w_each * int(qty))
        ))
        total += w_each * int(qty)
    return rows, round(total, 2)

def _compute_price(total_weight: float, distance_miles: float, move_date: str) -> Dict[str, Any]:
    crew = 2 if total_weight <= 1800 else 3 if total_weight <= 4000 else 4
    hours = max(2.5, 2.0 + (total_weight/800.0) + (distance_miles/100.0))
    mover_rate = float(os.getenv("HOURLY_RATE_PER_MOVER", "95"))
    truck_rate = float(os.getenv("TRUCK_RATE_PER_HOUR", "85"))
    labor = round((mover_rate * crew + truck_rate) * hours, 2)
    mile_rate = float(os.getenv("MILEAGE_RATE", "2.25"))
    mileage_cost = round(distance_miles * mile_rate, 2)
    m = int(move_date.split("-")[1])
    season = 1.08 if m in (5,6,7,8,9) else 1.03 if m in (12,1) else 1.00
    base_fee = float(os.getenv("BASE_FEE", "45"))
    subtotal = labor + mileage_cost
    total = round(subtotal * season + base_fee, 2)
    return {
        "crew_size": crew,
        "estimated_hours": round(hours, 2),
        "labor_cost": labor,
        "mileage_cost": mileage_cost,
        "season_multiplier": season,
        "base_fee": base_fee,
        "subtotal_before_season": round(subtotal, 2),
        "total_after_season_and_fees": total
    }

@api.post("/estimate")
def estimate(req: EstimateRequest, request: Request):
    idem = req.idempotency_key or request.headers.get("X-Request-Id")
    if idem and idem in _IDEMP:
        return _IDEMP[idem]

    rows, total_w = _resolve_items(req.items)
    logic = _compute_price(total_w, float(req.distance_miles), req.move_date)

    resp = {
        "inventory_breakdown": [r.model_dump() for r in rows],
        "calculation_logic": logic,
        "total_weight": total_w,
        "final_price": logic["total_after_season_and_fees"],
        "currency": "USD",
        "version": api.version
    }
    if idem:
        _IDEMP[idem] = resp
    return resp
