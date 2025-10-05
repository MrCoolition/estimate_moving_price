from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

BOX_SIZE_KEYS = ["1.5", "3.0", "4.5", "6.0", "wardrobe", "tv", "mirror"]
BOX_SYNONYMS = {
    "small box": "1.5",
    "medium box": "3.0",
    "large box": "4.5",
    "xl box": "6.0",
    "extra large box": "6.0",
    "wardrobe": "wardrobe",
    "wardrobe box": "wardrobe",
    "tv box": "tv",
    "flat screen": "tv",
    "mirror box": "mirror",
}
BOX_DISTRIBUTION = {
    "1.5": 0.5,
    "3.0": 0.35,
    "4.5": 0.1,
    "6.0": 0.05,
}


class LocationModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    location_type: str = Field(default="house")
    floor: int = Field(default=1, ge=0)
    elevator: bool = False
    stairs_flights: int = Field(default=0, ge=0)
    long_carry_feet: int = Field(default=0, ge=0)
    parking_distance_feet: int = Field(default=0, ge=0)


class PackingCartons(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cartons: Dict[str, int] = Field(default_factory=lambda: {key: 0 for key in BOX_SIZE_KEYS})

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value: Any) -> Dict[str, int]:
        if value is None:
            return {key: 0 for key in BOX_SIZE_KEYS}
        if isinstance(value, dict):
            normalized: Dict[str, int] = {key: 0 for key in BOX_SIZE_KEYS}
            for raw_key, raw_val in value.items():
                key = str(raw_key).lower()
                if key in BOX_SIZE_KEYS:
                    normalized[key] = int(raw_val)
                else:
                    mapped = BOX_SYNONYMS.get(key)
                    if mapped:
                        normalized[mapped] = int(raw_val)
            return normalized
        raise ValueError("cartons must be an object")

    def as_dict(self) -> Dict[str, int]:
        return dict(self.cartons)


class PackingOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    service: str = Field(default="none")
    cartons: PackingCartons = Field(default_factory=PackingCartons)

    def cartons_dict(self) -> Dict[str, int]:
        return self.cartons.as_dict()


class QuoteOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    optimize_for: str = Field(default="lowest_price")
    not_to_exceed: bool = False
    seasonality: str = Field(default="auto")


class EstimateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: List[str] = Field(default_factory=list)
    distance_miles: float = Field(..., ge=0)
    move_date: dt.date
    origin: LocationModel = Field(default_factory=LocationModel)
    destination: LocationModel = Field(default_factory=LocationModel)
    packing: PackingOptions = Field(default_factory=PackingOptions)
    options: QuoteOptions = Field(default_factory=QuoteOptions)
    idempotency_key: Optional[str] = None
    Qty: Optional[int] = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("Body must be an object")
        raw_items = value.get("items")
        qty_multiplier = value.get("Qty")
        counts: Counter[str] = Counter()
        if isinstance(raw_items, dict):
            for name, qty in raw_items.items():
                try:
                    counts[str(name)] += int(qty)
                except (TypeError, ValueError):
                    continue
        elif isinstance(raw_items, list):
            if raw_items and all(isinstance(elem, dict) for elem in raw_items):
                for elem in raw_items:
                    name = elem.get("item") or elem.get("name")
                    qty = elem.get("quantity") or elem.get("Qty") or 1
                    if name:
                        counts[str(name)] += int(qty)
            else:
                for elem in raw_items:
                    if isinstance(elem, str):
                        counts[elem] += 1
                    elif isinstance(elem, dict) and "name" in elem:
                        counts[str(elem["name"])] += int(elem.get("quantity") or 1)
        elif isinstance(raw_items, str):
            for piece in raw_items.split(","):
                piece = piece.strip()
                if not piece:
                    continue
                if ":" in piece:
                    name, qty = piece.split(":", 1)
                    try:
                        counts[name.strip()] += int(qty.strip())
                    except ValueError:
                        counts[name.strip()] += 1
                else:
                    counts[piece] += 1
        if isinstance(qty_multiplier, int) and qty_multiplier > 1 and counts:
            for key in list(counts.keys()):
                counts[key] *= qty_multiplier
        expanded: List[str] = []
        for name, qty in counts.items():
            expanded.extend([name] * max(int(qty), 0))
        value["items"] = expanded
        if "move_date" in value and not isinstance(value["move_date"], dt.date):
            value["move_date"] = _coerce_date(value["move_date"])
        return value

    def items_counter(self) -> Counter[str]:
        return Counter(self.items)


class EstimateResponse(BaseModel):
    quote_id: str
    final_price: float
    currency: str = "USD"
    breakdown_public: Dict[str, Any]
    line_items: List[Dict[str, Any]]
    version: str
    needs_clarification: bool = False
    clarification_items: Optional[List[Dict[str, Any]]] = None
    calculation_logic: Optional[Dict[str, Any]] = None


def _coerce_date(value: Any) -> dt.date:
    if isinstance(value, dt.date):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    string = str(value).strip().replace("/", "-")
    return dt.date.fromisoformat(string)


def distribute_boxes(total: int) -> Dict[str, int]:
    remaining = total
    distribution: Dict[str, int] = {}
    for code, ratio in BOX_DISTRIBUTION.items():
        qty = round(total * ratio)
        distribution[code] = qty
        remaining -= qty
    codes = list(BOX_DISTRIBUTION.keys())
    idx = 0
    while remaining > 0 and idx < len(codes):
        distribution[codes[idx]] += 1
        remaining -= 1
        idx = (idx + 1) % len(codes)
    return distribution


BOX_TOTAL_PATTERN = re.compile(r"(~|about)?\s*(\d+)\s+boxes?", re.IGNORECASE)


def detect_box_total(item_name: str) -> Optional[int]:
    match = BOX_TOTAL_PATTERN.search(item_name)
    if match:
        return int(match.group(2))
    return None
