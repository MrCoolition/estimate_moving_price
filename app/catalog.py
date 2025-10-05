from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, TypedDict


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


class Catalog:
    _WORD_RE = re.compile(r"[^a-z0-9\.]+")
    _BOX_KEYWORDS = {"box", "carton"}

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
            "aliases": ["wardrobe", "armoire wardrobe"],
        },
        {
            "id": "safe_large",
            "name": "Safe",
            "category": "safe",
            "volume_cuft": 18.0,
            "weight_lbs": 320.0,
            "aliases": ["safe", "gun safe", "floor safe"],
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
        self._alias_to_display: Dict[str, str] = {}
        self._alias_vectors: Dict[str, Counter[str]] = {}
        self._load_items(raw_items)

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
            seen_norms: set[str] = set()
            for alias in self._candidate_aliases(item):
                norm = self.normalize(alias)
                if not norm or norm in seen_norms:
                    continue
                seen_norms.add(norm)
                self.alias_to_id.setdefault(norm, item["id"])
                self._alias_to_display[norm] = alias
                self._alias_vectors[norm] = self._ngram_vector(norm)
        for alias, target_id in self._MANUAL_ALIASES.items():
            norm = self.normalize(alias)
            if not norm or target_id not in self.items:
                continue
            self.alias_to_id[norm] = target_id
            self._alias_to_display[norm] = alias
            self._alias_vectors[norm] = self._ngram_vector(norm)

    def _candidate_aliases(self, item: CatalogItem) -> Iterable[str]:
        yield item["name"]
        for alias in item.get("aliases", []):
            yield alias
        for alias in self._basic_alias_variants(item["name"]):
            yield alias
        tokens = self.normalize(item["name"]).split()
        if tokens and tokens[-1] in {"small", "medium", "large"}:
            trimmed = " ".join(tokens[:-1])
            if trimmed:
                yield trimmed
                parts = trimmed.split()
                if len(parts) == 2:
                    yield " ".join(reversed(parts))

    def _basic_alias_variants(self, name: str) -> List[str]:
        normalized = self.normalize(name)
        if not normalized:
            return []
        tokens = normalized.split()
        variants: List[str] = []
        if len(tokens) > 1:
            variants.append("_".join(tokens))
            variants.append("-".join(tokens))
            variants.append(" ".join(sorted(tokens)))
            if len(tokens) == 2:
                variants.append(" ".join(reversed(tokens)))
        return variants

    def normalize(self, raw: str) -> str:
        if not raw:
            return ""
        working = raw.strip().lower()
        working = working.replace("/", " ")
        tokens = [
            t for t in self._WORD_RE.split(working) if t
        ]
        normalized_tokens: List[str] = []
        for token in tokens:
            normalized_tokens.append(self._singularize(token))
        if any(tok in self._BOX_KEYWORDS for tok in normalized_tokens):
            normalized_tokens = sorted(normalized_tokens)
        return " ".join(normalized_tokens)

    def _singularize(self, token: str) -> str:
        if token.isdigit() or self._is_float_token(token):
            return token
        if token.endswith("ies") and len(token) > 3:
            return token[:-3] + "y"
        if token.endswith("ves") and len(token) > 3:
            return token[:-3] + "f"
        if token.endswith("ses") and len(token) > 3:
            return token[:-2]
        if token.endswith("xes") and len(token) > 3:
            return token[:-2]
        if token.endswith("s") and len(token) > 3:
            return token[:-1]
        return token

    def _is_float_token(self, token: str) -> bool:
        try:
            float(token)
        except ValueError:
            return False
        return True

    def match(self, raw: str, *, similarity_threshold: float = 0.92) -> Optional[MatchResult]:
        norm = self.normalize(raw)
        if not norm:
            return None
        item_id = self.alias_to_id.get(norm)
        if item_id:
            return MatchResult(
                item=self.items[item_id],
                alias=self._alias_to_display.get(norm, raw),
                normalized=norm,
                similarity=1.0,
            )
        vector = self._ngram_vector(norm)
        best: Optional[Tuple[float, str]] = None
        for alias_norm, alias_vec in self._alias_vectors.items():
            sim = self._cosine_similarity(vector, alias_vec)
            if sim == 0:
                continue
            if not best or sim > best[0]:
                best = (sim, alias_norm)
        if best and best[0] >= similarity_threshold:
            alias_norm = best[1]
            item_id = self.alias_to_id[alias_norm]
            return MatchResult(
                item=self.items[item_id],
                alias=self._alias_to_display.get(alias_norm, raw),
                normalized=alias_norm,
                similarity=best[0],
                approximate=True,
            )
        return None

    def suggest(self, raw: str, *, limit: int = 5) -> List[MatchResult]:
        norm = self.normalize(raw)
        if not norm:
            return []
        vector = self._ngram_vector(norm)
        scored: List[Tuple[float, str]] = []
        for alias_norm, alias_vec in self._alias_vectors.items():
            sim = self._cosine_similarity(vector, alias_vec)
            if sim <= 0:
                continue
            scored.append((sim, alias_norm))
        scored.sort(reverse=True, key=lambda x: x[0])
        results: List[MatchResult] = []
        for sim, alias_norm in scored[:limit]:
            item_id = self.alias_to_id[alias_norm]
            results.append(
                MatchResult(
                    item=self.items[item_id],
                    alias=self._alias_to_display.get(alias_norm, alias_norm),
                    normalized=alias_norm,
                    similarity=sim,
                    approximate=True,
                )
            )
        return results

    def _ngram_vector(self, norm: str, n: int = 3) -> Counter[str]:
        padded = f"{norm}"
        return Counter(padded[i : i + n] for i in range(max(len(padded) - n + 1, 1)))

    @staticmethod
    def _cosine_similarity(a: Counter[str], b: Counter[str]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(a[k] * b[k] for k in a.keys() & b.keys())
        if dot == 0:
            return 0.0
        norm_a = math.sqrt(sum(v * v for v in a.values()))
        norm_b = math.sqrt(sum(v * v for v in b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
