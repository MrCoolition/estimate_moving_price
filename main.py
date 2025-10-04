# main.py
from __future__ import annotations
import os
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple
from collections import Counter
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

# ───────── helpers ─────────

def _iso(d: Any) -> str:
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return datetime.fromisoformat(str(d).strip().replace("/", "-")).date().isoformat()

def _norm(s: str) -> str:
    return " ".join(str(s).strip().lower().replace("_", " ").split())

DEFAULT_WEIGHTS: Dict[str, float] = {
    "king size bed": 250.0,
    "refrigerator": 250.0,
    "dining table": 180.0,
    "dining chair": 25.0,
    "large rug": 50.0,
    "box small": 15.0,
    "box medium": 30.0,
    "box large": 45.0,
}

# ───────── request model ─────────

class EstimateRequest(BaseModel):
    items: Dict[str, int] = Field(..., description="Mapping item → quantity")
    distance_miles: float = Field(..., ge=0)
    move_date: str = Field(..., description="YYYY-MM-DD")
    idempotency_key: Optional[str] = None
    Qty: Optional[int] = None  # tolerated but ignored

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """Unwrap and normalize whatever ElevenLabs sends."""
        if not isinstance(values, dict):
            raise ValueError("Request body must be a JSON object")

        if "move_date" in values:
            values["move_date"] = _iso(values["move_date"])

        raw = values.get("items")

        # handle ElevenLabs wrapping like {"items":{"items":[...]}}
        if isinstance(raw, dict) and "items" in raw and isinstance(raw["items"], list):
            raw = raw["items"]

        # dict form
        if isinstance(raw, dict):
            values["items"] = {str(k): int(v) for k, v in raw.items() if str(v).isdigit()}
            return values

        # list of dicts
        if isinstance(raw, list) and raw and all(isinstance(e, dict) for e in raw):
            mapping: Dict[str, int] = {}
            for obj in raw:
                name = obj.get("item") or obj.get("items") or obj.get("name")
                qty = obj.get("Qty") or obj.get("qty") or obj.get("quantity") or 1
                if name:
                    iq = int(qty) if str(qty).isdigit() else 1
                    mapping[name] = mapping.get(name, 0) + iq
            values["items"] = mapping
            return values

        # list of strings
        if isinstance(raw, list):
            strings = [s for s in raw if isinstance(s, str) and s.strip()]
            if strings:
                counts = Counter(strings)
                qtop = values.get("Qty")
                if isinstance(qtop, int) and qtop > 1:
                    for k in list(counts.keys()):
                        counts[k] *= qtop
                values["items"] = dict(counts)
                return values

        # "a:1, b:2" string
        if isinstance(raw, str):
            mapping: Dict[str, int] = {}
            for pair in raw.split(","):
                pair = pair.strip()
                if not pair:
                    continue
                if ":" in pair:
                    name, qty = pair.split(":", 1)
                    mapping[name.strip()] = int(qty.strip()) if qty.strip().isdigit() else 1
                else:
                    mapping[pair] = mapping.get(pair, 0) + 1
            values["items"] = mapping
            return values

        raise ValueError("'items' must be list, dict, or string list")

# ───────── response DTOs ─────────

class InventoryRow(BaseModel):
    item_id: str
    name: str
    category: str
    quantity: int
    weight_each_lbs: float
    weight_total_lbs: float

# ───────── app ─────────

api = FastAPI(title="Estimate Moving Price", version="2025-10-04")
_IDEMP: Dict[str, Dict[str, Any]] = {}

@api.get("/", include_in_schema=False)
def health():
    return {"status": "ok"}

def _resolve_items(items: Dict[str, int]) -> Tuple[List[InventoryRow], float]:
    if not items:
        raise HTTPException(status_code=400, detail="No items provided")

    rows: List[InventoryRow] = []
    total = 0.0
    for name, qty in items.items():
        iq = int(qty) if str(qty).isdigit() else 0
        if iq <= 0:
            continue
        key = _norm(name)
        w_each = DEFAULT_WEIGHTS.get(key, 35.0)
        rows.append(InventoryRow(
            item_id=key.replace(" ", "_"),
            name=name,
            category="carton" if key.startswith("box_") or key.startswith("box ") else "misc",
            quantity=iq,
            weight_each_lbs=w_each,
            weight_total_lbs=w_each * iq
        ))
        total += w_each * iq
    if not rows:
        raise HTTPException(status_code=400, detail="No valid items after normalization")
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
