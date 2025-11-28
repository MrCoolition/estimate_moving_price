import asyncio
from typing import Any

import pytest

from app import orders
from app.orders import OrderEmailRequest, email_order


class DummySMTP:
    instances: list["DummySMTP"] = []

    def __init__(self, host: str, port: int, timeout: Any | None = None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.messages: list[Any] = []
        DummySMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username: str, password: str):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.messages.append(message)


def test_email_order_sends(monkeypatch):
    DummySMTP.instances.clear()
    monkeypatch.setenv("ORDER_EMAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ORDER_EMAIL_SMTP_PORT", "2525")
    monkeypatch.setenv("ORDER_EMAIL_RECIPIENTS", "ops@example.com, billing@example.com")
    monkeypatch.setenv("ORDER_EMAIL_SENDER", "estimates@example.com")
    monkeypatch.setenv("ORDER_EMAIL_SMTP_USERNAME", "user")
    monkeypatch.setenv("ORDER_EMAIL_SMTP_PASSWORD", "pass")

    monkeypatch.setattr(orders.smtplib, "SMTP", DummySMTP)

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

    smtp = DummySMTP.instances[0]
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 2525
    assert smtp.started_tls is True
    assert smtp.logged_in == ("user", "pass")
    assert smtp.messages, "Expected an email to be sent"

    email = smtp.messages[0]
    assert email["Subject"] == "New move lead from Alex Customer"
    assert email["From"] == "estimates@example.com"
    assert email["To"] == "ops@example.com, billing@example.com"
    assert email["Reply-To"] == "caller@example.com"
    assert "Sofa, 2 chairs" in email.get_content()


def test_email_order_requires_recipients(monkeypatch):
    monkeypatch.setenv("ORDER_EMAIL_SMTP_HOST", "smtp.example.com")
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
