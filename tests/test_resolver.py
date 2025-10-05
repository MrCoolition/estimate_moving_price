from collections import Counter
from pathlib import Path

import pytest

from app.catalog import Catalog
from app.resolver import ResolverOptions, resolve_inventory

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "estimation_weights_volumes_categories.json"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog(CATALOG_PATH)


def _aggregate(result) -> dict[str, int]:
    totals: dict[str, int] = {}
    for line in result.lines:
        totals[line.match.item["id"]] = totals.get(line.match.item["id"], 0) + line.quantity
    return totals


def test_rug_variants_resolve(catalog):
    result = resolve_inventory(Counter({"rug_large": 1, "rug large": 1}), catalog, ResolverOptions())
    totals = _aggregate(result)
    assert totals.get("rug_large") == 2
    assert result.match_summary["resolved_pct"] == 100


def test_bed_family_resolution(catalog):
    counter = Counter(
        {
            "bed_king_mattress": 3,
            "headboard": 3,
            "bed frame": 3,
            "box spring": 3,
        }
    )
    result = resolve_inventory(counter, catalog, ResolverOptions())
    totals = _aggregate(result)
    assert totals.get("bed_king_headboard") == 3
    assert totals.get("bed_king_frame") == 3
    assert totals.get("bed_king_box_spring") == 3
    size_assumptions = [entry for entry in result.assumptions if entry.get("type") == "size_inheritance"]
    assert size_assumptions and size_assumptions[0]["from"] == "bed_king_mattress"


def test_box_allocation_policy(catalog):
    result = resolve_inventory(Counter({"box": 10}), catalog, ResolverOptions())
    totals = _aggregate(result)
    assert totals.get("carton_box_small_1_5") == 5
    assert totals.get("carton_box_medium_3_0") == 3
    assert totals.get("carton_box_large_4_5") == 1
    assert totals.get("carton_box_xl_6_0") == 1
    box_assumptions = [entry for entry in result.assumptions if entry.get("type") == "box_distribution"]
    assert box_assumptions and sum(box_assumptions[0]["result"].values()) == 10


def test_fuzzy_and_backstop(catalog):
    options = ResolverOptions()
    result = resolve_inventory(Counter({"dreser": 1}), catalog, options)
    totals = _aggregate(result)
    assert totals.get("dresser_standard") == 1
    best_matches = [entry for entry in result.assumptions if entry.get("type") == "best_match" and entry.get("raw") == "dreser"]
    assert best_matches and best_matches[0]["confidence"] >= options.confidence_floor

    strict_options = ResolverOptions(confidence_floor=0.99)
    backstop = resolve_inventory(Counter({"enigmatic item": 1}), catalog, strict_options)
    assert any(entry.get("type") == "category_backstop" for entry in backstop.assumptions)


def test_resolver_deterministic(catalog):
    counter = Counter({"dining chair": 4, "sofa": 1, "rug_large": 1})
    first = resolve_inventory(counter, catalog, ResolverOptions())
    second = resolve_inventory(counter, catalog, ResolverOptions())
    assert _aggregate(first) == _aggregate(second)
