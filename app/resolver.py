from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from .catalog import AliasRecord, Catalog, CatalogItem, MatchResult
from .text_utils import normalize_label, tokenize, trigram_vector, cosine_similarity


BOX_ID_MAP = {
    "1.5": "carton_box_small_1_5",
    "3.0": "carton_box_medium_3_0",
    "4.5": "carton_box_large_4_5",
    "6.0": "carton_box_xl_6_0",
}

BED_SIZE_ORDER = ["king", "queen", "full", "twin"]
BED_SIZE_SYNONYMS = {
    "king": {"king", "cal king", "california"},
    "queen": {"queen"},
    "full": {"full", "double"},
    "twin": {"twin", "single"},
}
BED_SIZE_TARGETS = {
    "headboard": {
        "king": "bed_king_headboard",
        "queen": "bed_queen_headboard",
        "full": "bed_full_headboard",
        "twin": "bed_headboard",
    },
    "frame": {
        "king": "bed_king_frame",
        "queen": "bed_queen_frame",
        "full": "bed_double_full_frame",
        "twin": "bed_twin_single_frame",
    },
    "box_spring": {
        "king": "bed_king_box_spring",
        "queen": "bed_queen_box_spring",
        "full": "bed_double_full_box_spring",
        "twin": "bed_twin_single_box_spring",
    },
}

DRESSER_RULES = {
    "tall": "dresser_tall",
    "highboy": "dresser_tall",
    "chest": "dresser_tall",
    "double": "dresser_double",
    "lowboy": "dresser_double",
    "wide": "dresser_double",
}

CATEGORY_TOKEN_MAP = {
    "dresser": "dresser",
    "bureau": "dresser",
    "armoire": "wardrobe",
    "wardrobe": "wardrobe",
    "cabinet": "cabinet",
    "bench": "bench",
    "lamp": "lamp",
    "sofa": "sofa",
    "couch": "sofa",
    "sectional": "sofa",
    "table": "table",
    "chair": "chair",
    "stool": "chair",
    "piano": "piano",
    "rug": "rug",
    "bed": "bed",
    "mattress": "bed",
    "tv": "television",
    "television": "television",
    "mirror": "mirror",
    "desk": "desk",
    "appliance": "appliance",
    "refrigerator": "appliance",
    "fridge": "appliance",
    "freezer": "appliance",
    "box": "carton",
    "carton": "carton",
}


@dataclass
class ResolverOptions:
    resolver_policy: str = "best_match_no_fail"
    box_allocation_policy: str = "50/35/10/5"
    confidence_floor: float = 0.65
    assumptions_public: bool = True


@dataclass
class Candidate:
    record: AliasRecord
    score: float
    coverage: int
    category: str
    weight: float


@dataclass
class ResolvedLine:
    raw: str
    quantity: int
    match: MatchResult
    confidence: float
    reason: str
    alternates: List[Tuple[str, float]] = field(default_factory=list)


@dataclass
class ResolverResult:
    lines: List[ResolvedLine]
    assumptions: List[dict]
    match_summary: Dict[str, object]


def _parse_box_policy(policy: str) -> List[float]:
    ratios = []
    for chunk in policy.split("/"):
        try:
            ratios.append(float(chunk.strip()) / 100.0)
        except ValueError:
            ratios.append(0.0)
    while len(ratios) < 4:
        ratios.append(0.0)
    return ratios[:4]


def allocate_boxes(total: int, policy: str) -> Dict[str, int]:
    if total <= 0:
        return {size: 0 for size in BOX_ID_MAP}
    ratios = _parse_box_policy(policy)
    sizes = list(BOX_ID_MAP.keys())
    targets = [total * ratio for ratio in ratios]
    counts = [math.floor(value) for value in targets]
    remainder = total - sum(counts)
    remainders = sorted(
        ((idx, targets[idx] - counts[idx]) for idx in range(len(sizes))),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    for idx, _ in remainders:
        if remainder <= 0:
            break
        counts[idx] += 1
        remainder -= 1
    return {sizes[idx]: counts[idx] for idx in range(len(sizes))}


def infer_category(tokens: Sequence[str]) -> str:
    for token in tokens:
        category = CATEGORY_TOKEN_MAP.get(token)
        if category:
            return category
    return "misc"


def _token_set_ratio(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    if len(left_tokens) == 1 and len(right_tokens) == 1:
        return SequenceMatcher(None, next(iter(left_tokens)), next(iter(right_tokens))).ratio()
    overlap = len(left_tokens & right_tokens)
    total = len(left_tokens) + len(right_tokens)
    if total == 0:
        return 0.0
    return (2 * overlap) / total


def _partial_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _bed_majority(counter: Counter[str]) -> Optional[str]:
    votes = {size: 0 for size in BED_SIZE_ORDER}
    for raw, qty in counter.items():
        normalized = normalize_label(raw)
        if not normalized:
            continue
        tokens = set(tokenize(normalized))
        if not tokens & {"mattress", "mattres"}:
            continue
        for size, synonyms in BED_SIZE_SYNONYMS.items():
            if tokens & synonyms:
                votes[size] += qty
    best_size: Optional[str] = None
    best_votes = 0
    for size in BED_SIZE_ORDER:
        count = votes[size]
        if count > best_votes:
            best_size = size
            best_votes = count
    return best_size


def _family_bed_mapping(
    raw: str,
    tokens: Sequence[str],
    quantity: int,
    catalog: Catalog,
    size: Optional[str],
    applied: Dict[str, set],
) -> Optional[List[ResolvedLine]]:
    if not size:
        return None
    lower_tokens = set(tokens)
    if "headboard" in lower_tokens:
        target_map = BED_SIZE_TARGETS["headboard"]
        target_id = target_map.get(size)
        if target_id and catalog.get(target_id):
            applied.setdefault(size, set()).add(raw)
            return [_build_family_line(raw, quantity, catalog, target_id, "family_bed")]
    if "frame" in lower_tokens:
        target_map = BED_SIZE_TARGETS["frame"]
        target_id = target_map.get(size)
        if target_id and catalog.get(target_id):
            applied.setdefault(size, set()).add(raw)
            return [_build_family_line(raw, quantity, catalog, target_id, "family_bed")]
    if "box" in lower_tokens and "spring" in lower_tokens:
        target_map = BED_SIZE_TARGETS["box_spring"]
        target_id = target_map.get(size)
        if target_id and catalog.get(target_id):
            applied.setdefault(size, set()).add(raw)
            return [_build_family_line(raw, quantity, catalog, target_id, "family_bed")]
    return None


def _build_family_line(raw: str, quantity: int, catalog: Catalog, item_id: str, reason: str) -> ResolvedLine:
    item = catalog.get(item_id)
    if not item:
        raise ValueError(f"Missing catalog item for family rule: {item_id}")
    match = MatchResult(
        item=item,
        alias=item["name"],
        normalized=normalize_label(item["name"]),
        similarity=0.9,
        approximate=True,
    )
    return ResolvedLine(raw=raw, quantity=quantity, match=match, confidence=0.9, reason=reason)


def _family_dresser(raw: str, tokens: Sequence[str], quantity: int, catalog: Catalog) -> Optional[ResolvedLine]:
    lower_tokens = [token.lower() for token in tokens]
    for token in lower_tokens:
        mapped = DRESSER_RULES.get(token)
        if mapped and catalog.get(mapped):
            item = catalog.get(mapped)
            match = MatchResult(
                item=item,
                alias=item["name"],
                normalized=normalize_label(item["name"]),
                similarity=0.85,
                approximate=True,
            )
            return ResolvedLine(
                raw=raw,
                quantity=quantity,
                match=match,
                confidence=0.85,
                reason="family_dresser",
            )
    if {"dresser", "bureau"} & set(lower_tokens) and catalog.get("dresser_standard"):
        item = catalog.get("dresser_standard")
        match = MatchResult(
            item=item,
            alias=item["name"],
            normalized=normalize_label(item["name"]),
            similarity=0.82,
            approximate=True,
        )
        return ResolvedLine(
            raw=raw,
            quantity=quantity,
            match=match,
            confidence=0.82,
            reason="family_dresser",
        )
    return None


def _family_piano(
    raw: str,
    tokens: Sequence[str],
    quantity: int,
    catalog: Catalog,
    assumptions: List[dict],
) -> Optional[List[ResolvedLine]]:
    if "piano" not in tokens:
        return None
    normalized = " ".join(tokens)
    chosen_id = "piano_upright"
    confidence = 0.85
    if "grand" in tokens or "baby grand" in normalized:
        chosen_id = "piano_grand"
        confidence = 0.9
    elif tokens & {"upright", "spinet", "console", "studio"}:
        chosen_id = "piano_upright"
        confidence = 0.85
    lines: List[ResolvedLine] = []
    item = catalog.get(chosen_id)
    if item:
        match = MatchResult(
            item=item,
            alias=item["name"],
            normalized=normalize_label(item["name"]),
            similarity=confidence,
            approximate=True,
        )
        lines.append(
            ResolvedLine(
                raw=raw,
                quantity=quantity,
                match=match,
                confidence=confidence,
                reason="family_piano",
            )
        )
    if "bench" in tokens:
        bench_item = catalog.get("bench_piano")
        if bench_item:
            bench_match = MatchResult(
                item=bench_item,
                alias=bench_item["name"],
                normalized=normalize_label(bench_item["name"]),
                similarity=0.8,
                approximate=True,
            )
            bench_raw = f"{raw} (bench)"
            lines.append(
                ResolvedLine(
                    raw=bench_raw,
                    quantity=quantity,
                    match=bench_match,
                    confidence=0.8,
                    reason="family_piano",
                )
            )
            assumptions.append(
                {
                    "type": "best_match",
                    "raw": bench_raw,
                    "chosen_id": "bench_piano",
                    "confidence": 0.8,
                    "alternates": [],
                    "resolver": "family_piano",
                }
            )
    return lines or None


def _apply_boxes(
    raw: str,
    quantity: int,
    catalog: Catalog,
    options: ResolverOptions,
    assumptions: List[dict],
) -> List[ResolvedLine]:
    allocation = allocate_boxes(quantity, options.box_allocation_policy)
    assumptions.append(
        {
            "type": "box_distribution",
            "inputs": {"total_boxes": quantity, "policy": options.box_allocation_policy},
            "result": allocation,
        }
    )
    lines: List[ResolvedLine] = []
    for size, count in allocation.items():
        if count <= 0:
            continue
        item_id = BOX_ID_MAP[size]
        item = catalog.get(item_id)
        if not item:
            continue
        match = MatchResult(
            item=item,
            alias=item["name"],
            normalized=normalize_label(item["name"]),
            similarity=0.9,
            approximate=False,
        )
        lines.append(
            ResolvedLine(
                raw=f"{raw} ({size})",
                quantity=count,
                match=match,
                confidence=0.9,
                reason="box_distribution",
            )
        )
    return lines


def _candidate_score(norm: str, record: AliasRecord) -> float:
    token_ratio = _token_set_ratio(norm, record.normalized)
    partial_ratio = _partial_ratio(norm, record.normalized)
    cosine = cosine_similarity(trigram_vector(norm), record.vector)
    return (token_ratio + partial_ratio + cosine) / 3.0


def _candidate_coverage(tokens: Sequence[str], record: AliasRecord) -> int:
    alias_tokens = set(record.tokens)
    return sum(1 for token in tokens if token in alias_tokens)


def _deterministic_pick(
    candidates: List[Candidate],
    catalog: Catalog,
    category_hint: Optional[str],
) -> Candidate:
    def sort_key(candidate: Candidate) -> Tuple:
        item = catalog.get(candidate.record.item_id)
        category = item["category"] if item else ""
        medoid_id = catalog.category_medoid.get(category)
        median_weight = catalog.get(medoid_id)["weight_lbs"] if medoid_id and catalog.get(medoid_id) else 0.0
        weight_delta = abs(candidate.weight - median_weight)
        category_rank = 0 if category_hint and category == category_hint else 1
        return (
            -candidate.coverage,
            -candidate.score,
            category_rank,
            weight_delta,
            candidate.record.priority,
            candidate.record.item_id,
        )

    candidates.sort(key=sort_key)
    return candidates[0]


def _category_backstop(
    raw: str,
    tokens: Sequence[str],
    quantity: int,
    catalog: Catalog,
    assumptions: List[dict],
) -> ResolvedLine:
    category = infer_category(tokens)
    item_id = catalog.category_medoid.get(category)
    if not item_id and catalog.category_medoid:
        item_id = next(iter(catalog.category_medoid.values()))
    if not item_id:
        raise ValueError("Catalog is missing medoid definitions")
    item = catalog.get(item_id)
    match = MatchResult(
        item=item,
        alias=item["name"],
        normalized=normalize_label(item["name"]),
        similarity=0.5,
        approximate=True,
    )
    assumptions.append(
        {
            "type": "category_backstop",
            "category": category,
            "chosen_id": item_id,
            "reason": "no candidate >= floor",
        }
    )
    return ResolvedLine(
        raw=raw,
        quantity=quantity,
        match=match,
        confidence=0.5,
        reason="category_backstop",
    )


def resolve_inventory(
    counter: Counter[str],
    catalog: Catalog,
    options: ResolverOptions,
) -> ResolverResult:
    assumptions: List[dict] = []
    lines: List[ResolvedLine] = []
    size_applied: Dict[str, set] = {}
    bed_size = _bed_majority(counter)
    for raw, quantity in counter.items():
        if quantity <= 0:
            continue
        norm = normalize_label(raw)
        if not norm:
            continue
        tokens = tokenize(norm)
        if norm == "box":
            lines.extend(_apply_boxes(raw, quantity, catalog, options, assumptions))
            continue
        direct_item = catalog.get(raw)
        if direct_item:
            match = MatchResult(
                item=direct_item,
                alias=direct_item["name"],
                normalized=normalize_label(direct_item["name"]),
                similarity=1.0,
                approximate=False,
            )
            lines.append(
                ResolvedLine(
                    raw=raw,
                    quantity=quantity,
                    match=match,
                    confidence=1.0,
                    reason="exact_id",
                )
            )
            continue
        alias_id = catalog.alias_to_id.get(norm)
        if alias_id:
            item = catalog.get(alias_id)
            record = catalog.get_alias_record(norm)
            alias_name = record.alias if record else item["name"]
            lines.append(
                ResolvedLine(
                    raw=raw,
                    quantity=quantity,
                    match=MatchResult(
                        item=item,
                        alias=alias_name,
                        normalized=norm,
                        similarity=0.98,
                        approximate=False,
                    ),
                    confidence=0.98,
                    reason="alias_exact",
                )
            )
            continue
        family_lines = _family_bed_mapping(raw, tokens, quantity, catalog, bed_size, size_applied)
        if family_lines:
            lines.extend(family_lines)
            continue
        dresser_line = _family_dresser(raw, tokens, quantity, catalog)
        if dresser_line:
            lines.append(dresser_line)
            continue
        piano_lines = _family_piano(raw, tokens, quantity, catalog, assumptions)
        if piano_lines:
            lines.extend(piano_lines)
            continue
        candidates: List[Candidate] = []
        category_hint = infer_category(tokens)
        for record in catalog.alias_records():
            score = _candidate_score(norm, record)
            coverage = _candidate_coverage(tokens, record)
            if score < options.confidence_floor:
                continue
            item = catalog.get(record.item_id)
            candidates.append(
                Candidate(
                    record=record,
                    score=score,
                    coverage=coverage,
                    category=item["category"],
                    weight=item["weight_lbs"],
                )
            )
        if candidates:
            best = _deterministic_pick(candidates, catalog, category_hint)
            item = catalog.get(best.record.item_id)
            match = MatchResult(
                item=item,
                alias=best.record.alias,
                normalized=best.record.normalized,
                similarity=best.score,
                approximate=True,
            )
            alternates = [
                (cand.record.item_id, round(cand.score, 4))
                for cand in sorted(candidates, key=lambda c: c.score, reverse=True)[1:3]
            ]
            lines.append(
                ResolvedLine(
                    raw=raw,
                    quantity=quantity,
                    match=match,
                    confidence=best.score,
                    reason="fuzzy_match",
                    alternates=alternates,
                )
            )
            assumptions.append(
                {
                    "type": "best_match",
                    "raw": raw,
                    "chosen_id": item["id"],
                    "confidence": round(best.score, 4),
                    "alternates": alternates,
                    "resolver": "fuzzy",
                }
            )
            continue
        backstop_line = _category_backstop(raw, tokens, quantity, catalog, assumptions)
        lines.append(backstop_line)
        assumptions.append(
            {
                "type": "best_match",
                "raw": raw,
                "chosen_id": backstop_line.match.item["id"],
                "confidence": backstop_line.confidence,
                "alternates": [],
                "resolver": "category_backstop",
            }
        )
    for size, applied in size_applied.items():
        assumptions.append(
            {
                "type": "size_inheritance",
                "from": f"bed_{size}_mattress",
                "applied_to": sorted(applied),
            }
        )
    # ensure each resolved line has a best_match assumption if not already recorded
    recorded = {(entry.get("raw"), entry.get("chosen_id")) for entry in assumptions if entry.get("type") == "best_match"}
    for line in lines:
        key = (line.raw, line.match.item["id"])
        if key in recorded:
            continue
        assumptions.append(
            {
                "type": "best_match",
                "raw": line.raw,
                "chosen_id": line.match.item["id"],
                "confidence": round(line.confidence, 4),
                "alternates": line.alternates,
                "resolver": line.reason,
            }
        )
    low_confidence = sum(1 for line in lines if line.confidence < 0.75)
    summary = {
        "resolved_pct": 100,
        "low_confidence_count": low_confidence,
        "resolver_policy": options.resolver_policy,
    }
    return ResolverResult(lines=lines, assumptions=assumptions, match_summary=summary)
