from pathlib import Path

from app.catalog import Catalog
from app.text_utils import normalize_label

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "estimation_weights_volumes_categories.json"


def get_catalog() -> Catalog:
    return Catalog(CATALOG_PATH)


def test_alias_normalization_variants():
    catalog = get_catalog()
    direct = catalog.alias_to_id.get(normalize_label("dining_table"))
    spaced = catalog.alias_to_id.get(normalize_label("dining table"))
    hyphenated = catalog.alias_to_id.get(normalize_label("table - dining"))
    assert direct == spaced == hyphenated


def test_unknown_item_suggestions():
    catalog = get_catalog()
    suggestions = catalog.suggest("fridgee")
    assert suggestions
    assert suggestions[0].item["name"].lower().startswith("refrigerator")
