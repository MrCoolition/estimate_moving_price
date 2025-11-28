import asyncio
from pathlib import Path
from typing import Any

import pytest

from app import estimate_routes
from app.estimate_routes import OrderEmailRequest, email_order


class DummySES:
    def __init__(self, region_name: str | None, access_key: str | None, secret_key: str | None):
        self.region_name = region_name
        self.access_key = access_key
        self.secret_key = secret_key
        self.emails: list[dict[str, Any]] = []

    def send_email(self, **kwargs):
        self.emails.append(kwargs)


def test_email_order_sends(monkeypatch):
    ses_instances: dict[str, DummySES] = {}

    def fake_ses_client(service_name: str, region_name=None, aws_access_key_id=None, aws_secret_access_key=None):
        assert service_name == "ses"
        ses_instances["instance"] = DummySES(region_name, aws_access_key_id, aws_secret_access_key)
        return ses_instances["instance"]

    secrets_path = Path("/tmp/test-secrets.toml")
    secrets_path.write_text(
        "\n".join(
            [
                'ORDER_EMAIL_AWS_REGION = "us-east-1"',
                'ORDER_EMAIL_RECIPIENTS = ["ops@example.com", "billing@example.com"]',
                'ORDER_EMAIL_SENDER = "estimates@example.com"',
                'AWS_ACCESS_KEY_ID = "key-id"',
                'AWS_SECRET_ACCESS_KEY = "secret-key"',
            ]
        )
    )

    monkeypatch.setattr(estimate_routes, "DEFAULT_SECRET_PATHS", [secrets_path])
    monkeypatch.setattr(
        estimate_routes,
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
    assert ses.region_name == "us-east-1"
    assert ses.access_key == "key-id"
    assert ses.secret_key == "secret-key"
    assert ses.emails, "Expected an email to be sent"

    email = ses.emails[0]
    assert email["Source"] == "estimates@example.com"
    assert email["Destination"] == {"ToAddresses": ["ops@example.com", "billing@example.com"]}
    assert email["ReplyToAddresses"] == ["caller@example.com"]
    assert email["Message"]["Subject"]["Data"] == "New move lead from Alex Customer"
    assert "Sofa, 2 chairs" in email["Message"]["Body"]["Text"]["Data"]


def test_email_order_requires_recipients(monkeypatch):
    secrets_path = Path("/tmp/test-secrets-missing.toml")
    secrets_path.write_text('ORDER_EMAIL_AWS_REGION = "us-east-1"')

    monkeypatch.setattr(estimate_routes, "DEFAULT_SECRET_PATHS", [secrets_path])

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
