import asyncio
from typing import Any

import pytest

from app import orders
from app.orders import OrderSMSRequest, sms_order


class DummySNS:
    def __init__(self, region_name: str | None, access_key: str | None, secret_key: str | None):
        self.region_name = region_name
        self.access_key = access_key
        self.secret_key = secret_key
        self.published: list[dict[str, Any]] = []

    def publish(self, **kwargs):
        self.published.append(kwargs)


def test_sms_order_sends(monkeypatch):
    sns_instances: dict[str, DummySNS] = {}

    def fake_sns_client(service_name: str, region_name=None, aws_access_key_id=None, aws_secret_access_key=None):
        assert service_name == "sns"
        sns_instances["instance"] = DummySNS(region_name, aws_access_key_id, aws_secret_access_key)
        return sns_instances["instance"]

    monkeypatch.setenv("ORDER_SMS_AWS_REGION", "us-west-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "key-id")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setenv("ORDER_SMS_SENDER_ID", "MoverCo")

    monkeypatch.setattr(
        orders,
        "boto3",
        type("Boto3Stub", (), {"client": staticmethod(fake_sns_client)})(),
        raising=False,
    )

    payload = OrderSMSRequest(phone="+15551234567", message="Estimate ready", name="Alex Customer")

    response = asyncio.run(sms_order(payload))
    assert response == {"status": "sent", "phone": "+15551234567"}

    sns = sns_instances["instance"]
    assert sns.region_name == "us-west-2"
    assert sns.access_key == "key-id"
    assert sns.secret_key == "secret-key"
    assert sns.published, "Expected an SMS to be sent"

    message = sns.published[0]
    assert message["PhoneNumber"] == "+15551234567"
    assert message["Message"] == "Estimate ready"
    assert message["MessageAttributes"]["AWS.SNS.SMS.SenderID"]["StringValue"] == "MoverCo"


def test_sms_order_requires_region(monkeypatch):
    monkeypatch.delenv("ORDER_SMS_AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)

    payload = OrderSMSRequest(phone="+15551234567", message="Missing config", name="Alex")

    with pytest.raises(Exception) as excinfo:
        asyncio.run(sms_order(payload))

    assert "ORDER_SMS_AWS_REGION" in str(excinfo.value)
