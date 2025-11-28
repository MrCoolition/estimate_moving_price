from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from .catalog import Catalog, MatchResult


@dataclass
class FurnitureLineItem:
    item_id: str
    name: str
    quantity: int
    weight_each_lbs: float

    @property
    def total_weight_lbs(self) -> float:
        return round(self.weight_each_lbs * self.quantity, 2)

    def as_dict(self) -> Dict[str, object]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "quantity": self.quantity,
            "weight_each_lbs": round(self.weight_each_lbs, 2),
            "weight_total_lbs": self.total_weight_lbs,
        }


class FurnitureCatalog:
    """Lightweight wrapper to compute weights from the item catalog."""

    def __init__(self, catalog_path: str | Path):
        self._catalog = Catalog(catalog_path)

    def _match(self, key: str) -> MatchResult:
        match = self._catalog.match(key, similarity_threshold=0.85)
        if not match:
            raise ValueError(f"Item '{key}' not found in catalog")
        return match

    def total_weight(self, order: Dict[str, int]) -> Tuple[float, List[Dict[str, object]]]:
        total_weight = 0.0
        breakdown: List[Dict[str, object]] = []
        for raw_name, qty in order.items():
            quantity = int(qty)
            if quantity <= 0:
                continue
            match = self._match(str(raw_name))
            item = FurnitureLineItem(
                item_id=match.item["id"],
                name=match.item["name"],
                quantity=quantity,
                weight_each_lbs=float(match.item["weight_lbs"]),
            )
            total_weight += item.total_weight_lbs
            breakdown.append(item.as_dict())
        breakdown.sort(key=lambda entry: entry["name"].lower())
        return round(total_weight, 2), breakdown
