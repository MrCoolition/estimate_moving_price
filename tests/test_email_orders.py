import asyncio
from typing import Any

import pytest

from app import orders
from app.orders import OrderEmailRequest, email_order


class DummySES:
    def __init__(self, region_name: str | None, access_key: str | None, secret_key: str | None):
        self.region_name = region_name
        self.access_key = access_key
        self.secret_key = secret_key
        self.sent: list[dict[str, Any]] = []

    def send_email(self, **kwargs):
        self.sent.append(kwargs)


def test_email_order_sends(monkeypatch):
    ses_instances: dict[str, DummySES] = {}

    def fake_ses_client(service_name: str, region_name=None, aws_access_key_id=None, aws_secret_access_key=None):
        assert service_name == "ses"
        ses_instances["instance"] = DummySES(region_name, aws_access_key_id, aws_secret_access_key)
        return ses_instances["instance"]

    # Env for SES + recipients
    monkeypatch.setenv("ORDER_EMAIL_RECIPIENTS", "ops@example.com, billing@example.com")
    monkeypatch.setenv("ORDER_EMAIL_SENDER", "estimates@example.com")
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-key-id")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")

    monkeypatch.setattr(
        orders,
        "boto3",
        type("Boto3Stub", (), {"client": staticmethod(fake_ses_client)})(),
        raising=False,
    )

    payload = OrderEmailRequest(
        item_details="Sofa, 2 chairs",
        move_date="2025-06-20 (weekday)",
        phone="555-123-4567",
        locations="123 Main St -> 456 Oak St",
        estimate_price=1250.0,
        stairwells="Origin: 2nd floor walk-up; Destination: elevator",
        estimate_calculation_table="Item1: $500, Item2: $750",
        email="caller@example.com",
        name="Alex Customer",
    )

    response = asyncio.run(email_order(payload))
    assert response == {"status": "sent", "recipients": ["ops@example.com", "billing@example.com"]}

    ses = ses_instances["instance"]
    assert ses.region_name == "us-east-2"
    assert ses.access_key == "test-key-id"
    assert ses.secret_key == "test-secret-key"
    assert ses.sent, "Expected an email to be sent"

    call = ses.sent[0]
    assert call["Source"] == "estimates@example.com"
    assert call["Destination"]["ToAddresses"] == ["ops@example.com", "billing@example.com"]
    assert call["ReplyToAddresses"] == ["caller@example.com"]
    assert "Sofa, 2 chairs" in call["Message"]["Body"]["Text"]["Data"]


def test_email_order_requires_recipients(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.delenv("ORDER_EMAIL_RECIPIENTS", raising=False)

    payload = OrderEmailRequest(
        item_details="Desk",
        move_date="2025-06-20",
        phone="555-999-1111",
        locations="Origin -> Destination",
        estimate_price=500,
        stairwells="None",
        estimate_calculation_table="Table",
        email="caller@example.com",
        name="Case Missing",
    )

    with pytest.raises(Exception) as excinfo:
        asyncio.run(email_order(payload))

    assert "ORDER_EMAIL_RECIPIENTS" in str(excinfo.value)
