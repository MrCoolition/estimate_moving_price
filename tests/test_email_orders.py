import asyncio
from typing import Any

import smtplib
import pytest

from app.estimate_routes import OrderEmailRequest, email_order


class DummySMTP:
    def __init__(self, host: str, port: int, timeout: int | None = None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.tls_started = False
        self.login_called: tuple[str, str] | None = None
        self.messages: list[Any] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def starttls(self):
        self.tls_started = True

    def login(self, username: str, password: str):
        self.login_called = (username, password)

    def send_message(self, message):
        self.messages.append(message)


def test_email_order_sends(monkeypatch):
    smtp_instances: dict[str, DummySMTP] = {}

    def fake_smtp(host: str, port: int, timeout=None):
        smtp_instances["instance"] = DummySMTP(host, port, timeout)
        return smtp_instances["instance"]

    monkeypatch.setattr(smtplib, "SMTP", fake_smtp)
    monkeypatch.setenv("ORDER_EMAIL_RECIPIENTS", "ops@example.com,billing@example.com")
    monkeypatch.setenv("ORDER_EMAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ORDER_EMAIL_SMTP_USERNAME", "mailer@example.com")
    monkeypatch.setenv("ORDER_EMAIL_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("ORDER_EMAIL_SENDER", "estimates@example.com")

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

    smtp = smtp_instances["instance"]
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert smtp.tls_started is True
    assert smtp.login_called == ("mailer@example.com", "secret")
    assert smtp.messages, "Expected an email to be sent"

    message = smtp.messages[0]
    assert message["From"] == "estimates@example.com"
    assert message["To"] == "ops@example.com, billing@example.com"
    assert "New move lead from Alex Customer" == message["Subject"]
    assert "Sofa, 2 chairs" in message.get_content()


def test_email_order_requires_recipients(monkeypatch):
    monkeypatch.delenv("ORDER_EMAIL_RECIPIENTS", raising=False)
    monkeypatch.setenv("ORDER_EMAIL_SMTP_HOST", "smtp.example.com")

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
