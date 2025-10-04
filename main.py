# main.py
from __future__ import annotations
import os
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple
from collections import Counter

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

# ───────────────────────── helpers ─────────────────────────

def _iso(d: Any) -> str:
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    s = str(d).strip().replace("/", "-")
    return datetime.fromisoformat(s).date().isoformat()

def _norm(s: str) -> str:
    return " ".join(str(s).strip().lower().replace("_", " ").split())

# Fallback catalog (expand later)
DEFAULT_WEIGHTS: Dict[str, float] = {
    "king size bed": 250.0,
    "king size bed with box spring and headboard": 350.0,
    "box spring": 60.0,
    "headboard": 40.0,
    "regular refrigerator": 250.0,
    "refrigerator": 250.0,
    "refrigerator standard": 250.0,
    "dining room table with five chairs": 220.0,
    "dining table": 180.0,
    "dining table large": 220.0,
    "dining table large solid wood": 260.0,
    "chair": 25.0,
    "dining chair": 25.0,
    "bar stool": 15.0,
    "bar_stool": 15.0,
    "bed king mattress": 150.0,
    "bed_king_mattress": 150.0,
    "bed king box spring": 60.0,
    "bed_king_box_spring": 60.0,
    "bed king headboard": 40.0,
    "bed_king_headboard": 40.0,
    "large rug": 50.0,
    "large rolled rug": 55.0,
    "rug_large": 50.0,
    "rug_large_rolled": 55.0,
    "box small": 15.0,
    "box_small": 15.0,
    "box medium": 30.0,
    "box_medium": 30.0,
    "box large": 45.0,
    "box_large": 45.0,
}

# ───────────────────── request schema ─────────────────────

class EstimateRequest(BaseModel):
    # Canonicalized mapping after normalization
    items: Dict[str, int] = Field(..., description="Mapping item -> quantity")
    distance_miles: float = Field(..., ge=0)
    move_date: str = Field(..., description="YYYY-MM-DD")
    # Optional fields some clients might send:
    idempotency_key: Optional[str] = None
    Qty: Optional[int] = None  # seen at top-level from ElevenLabs UI in some configs

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """
        Accept ALL of the following and canonicalize to items: Dict[str,int]
        A) dict: {"items": {"refrigerator":2, "chair":5}}
        B) array<obj>: {"items": [{"item":"refrigerator","Qty":2}, ...]}
        C) array<str>: {"items": ["refrigerator","chair","chair", ...], "Qty": 1 (optional)}
        D) string list format: {"items":"refrigerator:2, chair:5"}  (belt & suspenders)
        """
        if not isinstance(values, dict):
            raise ValueError("Request body must be a JSON object")

        # normalize move_date
        if "move_date" in values:
            values["move_date"] = _iso(values["move_date"])

        raw = values.get("items")

        # D) string list "a:1, b:2"
        if isinstance(raw, str):
            mapping: Dict[str, int] = {}
            for pair in raw.split(","):
                pair = pair.strip()
                if not pair:
                    continue
                if ":" in pair:
                    name, qty = pair.split(":", 1)
                    mapping[name.strip()] = int(str(qty).strip())
                else:
                    # if no qty provided, count as 1
                    mapping[pair] = mapping.get(pair, 0) + 1
            values["items"] = mapping
            return values

        # A) dict mapping
        if isinstance(raw, dict):
            mapping = {}
            for k, v in raw.items():
                try:
                    iv = int(v)
                except Exception:
                    continue
                if iv > 0:
                    mapping[str(k)] = iv
            values["items"] = mapping
            return values

        # B) array of objects {item, Qty}
        if isinstance(raw, list) and raw and all(isinstance(e, dict) for e in raw):
            mapping: Dict[str, int] = {}
            for obj in raw:
                name = obj.get("item") or obj.get("items") or obj.get("name")
                qty = obj.get("Qty") or obj.get("qty") or obj.get("quantity") or 1
                if name:
                    try:
                        iq = int(qty)
                    except Exception:
                        iq = 1
                    if iq > 0:
                        mapping[str(name)] = mapping.get(str(name), 0) + iq
            values["items"] = mapping
            return values

        # C) array of strings (with optional top-level Qty or duplicates)
        if isinstance(raw, list) and all(isinstance(e, str) for e in raw):
            counts = Counter([s for s in raw if s and isinstance(s, str)])
            # If a top-level Qty is present and >1, multiply each unique by that default
            default_qty = values.get("Qty")
            if isinstance(default_qty, int) and default_qty > 1:
                for k in list(counts.keys()):
                    counts[k] = counts[k] * default_qty
            values["items"] = {k: int(v) for k, v in counts.items() if int(v) > 0}
            return values

        # Empty or unrecognized
        raise ValueError("'items' must be a dict, array of objects, array of strings, or a string list like 'a:1, b:2'")

# ───────────────────── response DTOs ─────────────────────

class InventoryRow(BaseModel):
    item_id: str
    name: str
    category: str
    quantity: int
    weight_each_lbs: float
    weight_total_lbs: float

# ───────────────────────── app ───────────────────────────

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
        try:
            iq = int(qty)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid quantity for '{name}'")
        if iq <= 0:
            raise HTTPException(status_code=400, detail="Quantities must be positive integers")

        key = _norm(name)
        w_each = DEFAULT_WEIGHTS.get(key, 35.0)

        rows.append(InventoryRow(
            item_id=key.replace(" ", "_"),
            name=name,
            category="carton" if key.startswith("box_") or key.startswith("box ") else "misc",
            quantity=iq,
            weight_each_lbs=float(w_each),
            weight_total_lbs=float(w_each * iq)
        ))
        total += w_each * iq
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
    # Idempotency (optional)
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
