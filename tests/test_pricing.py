import json
from datetime import date
from pathlib import Path

from app.catalog import Catalog
from app.packing import PackingCatalog
from app.pricing import (
    ItemAllocation,
    LocationContext,
    PackingRequest,
    QuoteContext,
    QuoteOptions,
    optimize,
)
from app.rules import load_rules

BASE_DIR = Path(__file__).resolve().parent.parent
CATALOG_PATH = BASE_DIR / "data" / "estimation_weights_volumes_categories.json"
RULES_PATH = BASE_DIR / "data" / "moving_rules.json"
PACKING_PATH = BASE_DIR / "data" / "packing_weight_volume_pricing.tsv"


def build_context(items: list[tuple[str, int]], distance: float = 10.0) -> QuoteContext:
    catalog = Catalog(CATALOG_PATH)
    rules = load_rules(RULES_PATH)
    with RULES_PATH.open("r", encoding="utf-8") as fh:
        rules_payload = json.load(fh)
    packing_catalog = PackingCatalog(
        tsv_path=PACKING_PATH,
        json_config=rules_payload["movingQuoterContext"],
    )
    allocations: list[ItemAllocation] = []
    for name, quantity in items:
        match = catalog.match(name)
        assert match is not None, f"Catalog missing item {name}"
        allocations.append(ItemAllocation(match=match, quantity=quantity))
    origin_raw = {"location_type": "house", "floor": 1}
    dest_raw = {"location_type": "house", "floor": 1}
    origin = LocationContext(raw=origin_raw, access_rule=rules.access_for_location(origin_raw))
    destination = LocationContext(raw=dest_raw, access_rule=rules.access_for_location(dest_raw))
    return QuoteContext(
        move_date=date(2025, 11, 1),
        distance_miles=distance,
        origin=origin,
        destination=destination,
        allocations=allocations,
        rules=rules,
        packing_catalog=packing_catalog,
        packing_request=PackingRequest(service="none", cartons={}),
        options=QuoteOptions(),
    )


def test_minimum_hours_enforced():
    ctx = build_context([("dining chair", 4), ("dining table", 1)])
    quote, _ = optimize(ctx)
    assert quote.billable_hours >= ctx.rules.min_billable_hours
    assert quote.movers == ctx.rules.min_movers


def test_heavy_load_scales_movers_and_trucks():
    ctx = build_context([("sofa", 4), ("refrigerator", 2), ("wardrobe", 4), ("safe", 2)], distance=40)
    quote, _ = optimize(ctx)
    assert quote.movers >= 3
    assert quote.trucks >= 1
    assert quote.billable_hours >= ctx.rules.min_billable_hours
