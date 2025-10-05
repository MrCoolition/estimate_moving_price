from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, TypedDict

from .text_utils import cosine_similarity, generate_tokens, normalize_label, trigram_vector


class CatalogItem(TypedDict):
    id: str
    name: str
    category: str
    volume_cuft: float
    weight_lbs: float
    aliases: List[str]


@dataclass
class MatchResult:
    item: CatalogItem
    alias: str
    normalized: str
    similarity: float
    approximate: bool = False


@dataclass(frozen=True)
class AliasRecord:
    item_id: str
    alias: str
    normalized: str
    tokens: Sequence[str]
    vector: Counter[str]
    priority: int


class Catalog:
    _MANUAL_OVERRIDES: List[Dict[str, object]] = [
        {
            "id": "refrigerator_standard",
            "name": "Refrigerator",
            "category": "appliance",
            "volume_cuft": 45.0,
            "weight_lbs": 250.0,
            "aliases": ["refrigerator", "fridge", "refrigerator standard"],
        },
        {
            "id": "sofa_three_seat",
            "name": "Sofa",
            "category": "sofa",
            "volume_cuft": 65.0,
            "weight_lbs": 210.0,
            "aliases": ["sofa", "couch", "sofa couch"],
        },
        {
            "id": "wardrobe_large",
            "name": "Wardrobe",
            "category": "wardrobe",
            "volume_cuft": 45.0,
            "weight_lbs": 240.0,
            "aliases": ["wardrobe", "armoire", "armoire wardrobe"],
        },
        {
            "id": "safe_large",
            "name": "Safe",
            "category": "safe",
            "volume_cuft": 18.0,
            "weight_lbs": 320.0,
            "aliases": ["safe", "gun safe", "floor safe"],
        },
        {
            "id": "dresser_standard",
            "name": "Dresser",
            "category": "dresser",
            "volume_cuft": 35.0,
            "weight_lbs": 150.0,
            "aliases": ["dresser", "bureau"],
        },
        {
            "id": "dresser_tall",
            "name": "Dresser Tall",
            "category": "dresser",
            "volume_cuft": 32.0,
            "weight_lbs": 165.0,
            "aliases": ["tall dresser", "highboy", "chest of drawers"],
        },
        {
            "id": "dresser_double",
            "name": "Dresser Double",
            "category": "dresser",
            "volume_cuft": 45.0,
            "weight_lbs": 190.0,
            "aliases": ["double dresser", "lowboy dresser"],
        },
        {
            "id": "rug_large",
            "name": "Rug Large",
            "category": "rug",
            "volume_cuft": 10.0,
            "weight_lbs": 50.0,
            "aliases": ["large rug", "rug large"],
        },
        {
            "id": "carton_box_small_1_5",
            "name": "Box Small 1.5 cu ft",
            "category": "carton",
            "volume_cuft": 1.5,
            "weight_lbs": 35.0,
            "aliases": ["1.5 box", "small box", "box 1.5", "1.5 cu ft box"],
        },
        {
            "id": "carton_box_medium_3_0",
            "name": "Box Medium 3.0 cu ft",
            "category": "carton",
            "volume_cuft": 3.0,
            "weight_lbs": 50.0,
            "aliases": ["3.0 box", "medium box", "box 3.0", "3.0 cu ft box"],
        },
        {
            "id": "carton_box_large_4_5",
            "name": "Box Large 4.5 cu ft",
            "category": "carton",
            "volume_cuft": 4.5,
            "weight_lbs": 65.0,
            "aliases": ["4.5 box", "large box", "box 4.5", "4.5 cu ft box"],
        },
        {
            "id": "carton_box_xl_6_0",
            "name": "Box XL 6.0 cu ft",
            "category": "carton",
            "volume_cuft": 6.0,
            "weight_lbs": 80.0,
            "aliases": ["6.0 box", "xl box", "extra large box", "box 6.0"],
        },
    ]

    _MANUAL_ALIASES: Dict[str, str] = {
        "dining table": "dining_table_medium",
        "table dining": "dining_table_medium",
        "dining table medium": "dining_table_medium",
        "refrigerator": "refrigerator_standard",
        "fridge": "refrigerator_standard",
        "couch": "sofa_three_seat",
        "sofa": "sofa_three_seat",
        "wardrobe": "wardrobe_large",
        "safe": "safe_large",
        "bureau": "dresser_standard",
        "dresser": "dresser_standard",
    }

    def __init__(self, path: str | Path):
        self._path = Path(path)
        if not self._path.exists():
            raise FileNotFoundError(f"Catalog file not found: {self._path}")
        with self._path.open("r", encoding="utf-8") as fh:
            raw_items = list(json.load(fh))
        raw_items.extend(self._MANUAL_OVERRIDES)
        self.items: Dict[str, CatalogItem] = {}
        self.alias_to_id: Dict[str, str] = {}
        self._alias_records: Dict[str, AliasRecord] = {}
        self._load_items(raw_items)
        self._alias_record_list: Sequence[AliasRecord] = tuple(self._alias_records.values())
        self.category_medoid: Dict[str, str] = self._compute_category_medoids()

    def _register_alias(self, alias: str, item: CatalogItem, *, priority: int) -> None:
        normalized = normalize_label(alias)
        if not normalized:
            return
        record = AliasRecord(
            item_id=item["id"],
            alias=alias,
            normalized=normalized,
            tokens=generate_tokens(normalized),
            vector=trigram_vector(normalized),
            priority=priority,
        )
        existing = self._alias_records.get(normalized)
        if existing and existing.priority <= priority:
            return
        self._alias_records[normalized] = record
        self.alias_to_id[normalized] = item["id"]

    def _load_items(self, raw_items: Iterable[dict]) -> None:
        for obj in raw_items:
            item: CatalogItem = {
                "id": obj["id"],
                "name": obj["name"],
                "category": obj.get("category", "misc"),
                "volume_cuft": float(obj.get("volume_cuft", 0.0)),
                "weight_lbs": float(obj.get("weight_lbs", 0.0)),
                "aliases": list(obj.get("aliases", [])),
            }
            self.items[item["id"]] = item
            self._register_alias(item["name"], item, priority=0)
            for alias in item.get("aliases", []):
                self._register_alias(alias, item, priority=1)
            for variant in self._basic_alias_variants(item["name"]):
                self._register_alias(variant, item, priority=2)
        for alias, target_id in self._MANUAL_ALIASES.items():
            item = self.items.get(target_id)
            if not item:
                continue
            self._register_alias(alias, item, priority=0)

    def _basic_alias_variants(self, name: str) -> Sequence[str]:
        normalized = normalize_label(name)
        if not normalized:
            return []
        parts = normalized.split()
        variants: List[str] = []
        if len(parts) == 2:
            variants.append(" ".join(reversed(parts)))
        variants.append("_".join(parts))
        variants.append("-".join(parts))
        return variants

    def _compute_category_medoids(self) -> Dict[str, str]:
        by_category: Dict[str, List[CatalogItem]] = defaultdict(list)
        for item in self.items.values():
            by_category[item["category"]].append(item)
        medoids: Dict[str, str] = {}
        for category, items in by_category.items():
            if not items:
                continue
            sorted_items = sorted(items, key=lambda itm: (itm["weight_lbs"], itm["id"]))
            median_index = len(sorted_items) // 2
            target_weight = sorted_items[median_index]["weight_lbs"]
            medoid = min(
                sorted_items,
                key=lambda itm: (abs(itm["weight_lbs"] - target_weight), itm["id"]),
            )
            medoids[category] = medoid["id"]
        return medoids

    def get(self, item_id: str) -> Optional[CatalogItem]:
        return self.items.get(item_id)

    def alias_records(self) -> Sequence[AliasRecord]:
        return self._alias_record_list

    def get_alias_record(self, normalized: str) -> Optional[AliasRecord]:
        return self._alias_records.get(normalized)

    def match(self, raw: str, *, similarity_threshold: float = 0.92) -> Optional[MatchResult]:
        normalized = normalize_label(raw)
        if not normalized:
            return None
        item_id = self.alias_to_id.get(normalized)
        if item_id:
            item = self.items[item_id]
            record = self._alias_records[normalized]
            return MatchResult(item=item, alias=record.alias, normalized=normalized, similarity=1.0)
        vector = trigram_vector(normalized)
        best: Optional[AliasRecord] = None
        best_score = 0.0
        for record in self._alias_records.values():
            score = cosine_similarity(vector, record.vector)
            if score > best_score:
                best_score = score
                best = record
        if best and best_score >= similarity_threshold:
            item = self.items[best.item_id]
            return MatchResult(
                item=item,
                alias=best.alias,
                normalized=best.normalized,
                similarity=best_score,
                approximate=True,
            )
        return None

    def suggest(self, raw: str, *, limit: int = 5) -> List[MatchResult]:
        normalized = normalize_label(raw)
        if not normalized:
            return []
        vector = trigram_vector(normalized)
        scored: List[tuple[float, AliasRecord]] = []
        for record in self._alias_records.values():
            score = cosine_similarity(vector, record.vector)
            if score <= 0:
                continue
            scored.append((score, record))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results: List[MatchResult] = []
        for score, record in scored[:limit]:
            item = self.items[record.item_id]
            results.append(
                MatchResult(
                    item=item,
                    alias=record.alias,
                    normalized=record.normalized,
                    similarity=score,
                    approximate=True,
                )
            )
        return results
