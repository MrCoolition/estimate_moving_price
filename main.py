from __future__ import annotations
import os, json
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from difflib import get_close_matches
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

DATA_DIR = Path(__file__).parent / "data"
CATALOG_PATH = DATA_DIR / "estimation_weights_volumes_categories.json"

try:
    with open(CATALOG_PATH, "r") as f:
        _CATALOG_LIST = json.load(f)
except FileNotFoundError:
    _CATALOG_LIST = []

ITEM_CATALOG: Dict[str, Dict[str, Any]] = {}
for rec in _CATALOG_LIST:
    ITEM_CATALOG[rec["id"].lower()] = rec
    ITEM_CATALOG[rec["name"].lower()] = rec
    for a in rec.get("aliases", []) or []:
        ITEM_CATALOG[a.lower()] = rec

CARTON_WEIGHTS = {
    "box_small": 15.0,
    "box_medium": 30.0,
    "box_large": 45.0,
    "box_xlarge": 55.0,
    "wardrobe_box": 50.0,
    "dish_pack": 45.0,
}

def _norm(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")

def _iso(d: Any) -> str:
    if isinstance(d, (date, datetime)):
        return (d.date() if isinstance(d, datetime) else d).isoformat()
    s = str(d).strip().replace("/", "-")
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        y, m, dd = s.split("-")
        return datetime(int(y), int(m), int(dd)).date().isoformat()

class EstimateRequest(BaseModel):
    items: Dict[str, int] = Field(..., description="Items and quantities")
    distance_miles: float = Field(..., ge=0)
    move_date: str = Field(..., description="YYYY-MM-DD")
    idempotency_key: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, data: Any) -> Any:
        if isinstance(data, list):
            if len(data) == 1 and isinstance(data[0], dict):
                data = data[0]
            else:
                raise ValueError("Request body must be a JSON object")
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object")

        items = data.get("items")
        if items is None:
            raise ValueError("Missing 'items'")
        if isinstance(items, list):
            converted: Dict[str, int] = {}
            for entry in items:
                if not isinstance(entry, dict):
                    raise ValueError("items list must contain objects")
                name = entry.get("name") or entry.get("item") or entry.get("items")
                qty  = entry.get("Qty")  or entry.get("qty")  or entry.get("quantity") or entry.get("q")
                if name is None or qty is None:
                    raise ValueError("items list entries must have name and quantity")
                converted[str(name)] = int(qty)
            data["items"] = converted
        elif not isinstance(items, dict):
            raise ValueError("'items' must be an object or list")

        for k in ("distance_miles", "move_date"):
            if data.get(k) is None and k in data["items"]:
                data[k] = data["items"].pop(k)

        if isinstance(data.get("distance_miles"), str):
            data["distance_miles"] = float(data["distance_miles"])
        data["move_date"] = _iso(data["move_date"])
        return data

def _resolve_items(items: Dict[str, int]) -> Tuple[List[Dict[str, Any]], float]:
    breakdown: List[Dict[str, Any]] = []
    total_w = 0.0
    for name, qty in items.items():
        if not isinstance(qty, int) or qty <= 0:
            raise HTTPException(status_code=400, detail="Quantity must be a positive integer")
        key = _norm(name)
        rec = ITEM_CATALOG.get(key)
        if rec is None:
            matches = get_close_matches(key, list(ITEM_CATALOG.keys()), n=1, cutoff=0.88)
            if matches:
                rec = ITEM_CATALOG[matches[0]]
        if rec is None:
            if key in CARTON_WEIGHTS:
                w_each = CARTON_WEIGHTS[key]
                breakdown.append({"item_id": key, "name": key.replace("_"," ").title(),
                                  "category": "carton", "quantity": qty,
                                  "weight_each_lbs": float(w_each),
                                  "weight_total_lbs": float(qty*w_each)})
                total_w += qty*w_each
                continue
            w_each = 35.0
            breakdown.append({"item_id": key, "name": name, "category": "misc",
                              "quantity": qty, "weight_each_lbs": w_each,
                              "weight_total_lbs": float(qty*w_each),
                              "note":"unknown item; default weight applied"})
            total_w += qty*w_each
            continue
        w_each = float(rec.get("weight_lbs", 35.0))
        breakdown.append({"item_id": rec.get("id", key), "name": rec.get("name", name),
                          "category": rec.get("category", "misc"),
                          "quantity": qty, "weight_each_lbs": w_each,
                          "weight_total_lbs": float(qty*w_each)})
        total_w += qty*w_each
    return breakdown, float(round(total_w, 2))

def _compute_price(total_weight: float, distance_miles: float, move_date: str) -> Dict[str, Any]:
    crew = 2 if total_weight <= 1800 else 3 if total_weight <= 4000 else 4
    hours = max(2.5, 2.0 + (total_weight/800.0) + (distance_miles/100.0))
    hr = float(os.getenv("HOURLY_RATE_PER_MOVER", "95"))
    labor = round(crew * hr * hours, 2)
    truck_base = float(os.getenv("TRUCK_BASE", "120"))
    mile_rate  = float(os.getenv("MILEAGE_RATE", "2.25"))
    truck = round(truck_base + distance_miles * mile_rate, 2)
    m = int(move_date.split("-")[1])
    season = 1.08 if m in (5,6,7,8,9) else 1.03 if m in (12,1) else 1.00
    base_fee = float(os.getenv("BASE_FEE", "45"))
    subtotal = labor + truck
    total = round(subtotal * season + base_fee, 2)
    return {
        "crew_size": crew,
        "estimated_hours": round(hours, 2),
        "billable_hours": round(hours, 2),
        "labor_cost": labor,
        "truck_cost": truck,
        "mileage_cost": round(distance_miles*mile_rate, 2),
        "season_multiplier": season,
        "base_fee": base_fee,
        "subtotal_before_season": round(subtotal, 2),
        "total_after_season_and_fees": total,
    }

api = FastAPI(title="Estimate Moving Price", version="2025-10-04")
_IDEMP: Dict[str, Dict[str, Any]] = {}

@api.get("/", include_in_schema=False)
def health():
    return {"status":"ok"}

@api.post("/estimate")
def estimate(req: EstimateRequest, request: Request):
    idem = req.idempotency_key or request.headers.get("X-Request-Id")
    if idem and idem in _IDEMP:
        return _IDEMP[idem]
    breakdown, total_w = _resolve_items(req.items)
    logic = _compute_price(total_w, float(req.distance_miles), req.move_date)
    resp = {
        "inventory_breakdown": breakdown,
        "calculation_logic": logic,
        "total_weight": total_w,
        "final_price": logic["total_after_season_and_fees"],
        "currency":"USD",
        "version": api.version
    }
    if idem:
        _IDEMP[idem] = resp
    return resp
