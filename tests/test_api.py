import hashlib
import hashlib
import hmac
import asyncio

from app.estimate_routes import EstimateRequest, create_estimate


def test_estimate_endpoint_success():
    payload = EstimateRequest(
        items={
            "bed_king_mattress": 1,
            "bar_stool": 4,
            "refrigerator": 1,
        },
        distance_miles=15,
        move_date="2025-07-08",
    )
    data = asyncio.run(create_estimate(payload))
    assert data["total_price"] > 0
    assert data["billable_hours"] >= 3.0
    assert data["movers"] >= 2
    assert data["inventory_breakdown"]
