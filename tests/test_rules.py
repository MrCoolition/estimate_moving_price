from pathlib import Path

from app.rules import load_rules

RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "moving_rules.json"


def get_rules():
    return load_rules(RULES_PATH)


def test_access_rule_selection_house_with_stairs():
    rules = get_rules()
    access = rules.access_for_location({"location_type": "house", "stairs_flights": 2, "floor": 2})
    assert access.code == "1A"


def test_access_rule_selection_apartment_second_floor():
    rules = get_rules()
    access = rules.access_for_location({"location_type": "apartment", "floor": 2, "elevator": False})
    assert access.code == "1B"
    ground = rules.access_for_location({"location_type": "apartment", "floor": 1})
    assert ground.code == "1C"
