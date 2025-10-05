import hashlib
import hmac
import json

import pytest

import main
from app.security import HMACVerifier
from tests.conftest import call_estimate


def _sign(body: dict, secret: str) -> tuple[str, bytes]:
    payload = json.dumps(body).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}", payload


def test_estimate_endpoint_success():
    payload = {
        "items": [
            "refrigerator",
            "dining_table",
            *["dining_chair"] * 7,
        ],
        "distance_miles": 12,
        "move_date": "2025-11-01",
        "origin": {"location_type": "house", "floor": 1},
        "destination": {"location_type": "house", "floor": 1},
        "packing": {"service": "none"},
    }
    response = call_estimate(payload)
    data = response.model_dump()
    assert data["final_price"] > 0
    assert data["breakdown_public"]["movers"] == 2
    assert data["breakdown_public"]["labor_hours_billed"] >= 3.0


def test_idempotency_returns_cached_response():
    payload = {
        "items": ["refrigerator"],
        "distance_miles": 5,
        "move_date": "2025-05-10",
        "origin": {"location_type": "house", "floor": 1},
        "destination": {"location_type": "house", "floor": 1},
        "packing": {"service": "none"},
    }
    headers = {"Idempotency-Key": "abc123"}
    first = call_estimate(payload, headers=headers).model_dump()
    second = call_estimate(payload, headers=headers).model_dump()
    assert first == second


def test_hmac_enforced(monkeypatch):
    secret = "supersecret"
    monkeypatch.setenv("HMAC_SECRET", secret)
    monkeypatch.setattr(main, "verifier", HMACVerifier(secret))
    payload = {
        "items": ["refrigerator"],
        "distance_miles": 5,
        "move_date": "2025-05-10",
        "origin": {"location_type": "house", "floor": 1},
        "destination": {"location_type": "house", "floor": 1},
        "packing": {"service": "none"},
    }
    signature, body = _sign(payload, secret)
    ok = call_estimate(payload, headers={"X-Signature": signature})
    assert ok.final_price > 0
    with pytest.raises(Exception):
        call_estimate(payload, headers={"X-Signature": "sha256=deadbeef"})
