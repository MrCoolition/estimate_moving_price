from pathlib import Path

from app.catalog import Catalog

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "estimation_weights_volumes_categories.json"


def get_catalog() -> Catalog:
    return Catalog(CATALOG_PATH)


def test_alias_normalization_variants():
    catalog = get_catalog()
    direct = catalog.match("dining_table")
    spaced = catalog.match("dining table")
    hyphenated = catalog.match("table - dining")
    assert direct is not None
    assert spaced is not None
    assert hyphenated is not None
    assert direct.item["id"] == spaced.item["id"] == hyphenated.item["id"]


def test_unknown_item_suggestions():
    catalog = get_catalog()
    match = catalog.match("fridgee")
    assert match is None
    suggestions = catalog.suggest("fridgee")
    assert suggestions
    assert suggestions[0].item["name"].lower().startswith("refrigerator")
