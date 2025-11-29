from __future__ import annotations

import os
import re
from typing import Any, Dict, List

try:  # pragma: no cover - import guard for environments without AWS SDK
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ModuleNotFoundError:  # pragma: no cover
    boto3 = None  # type: ignore

    class BotoCoreError(Exception):
        """Fallback error when botocore is unavailable."""

    class ClientError(Exception):
        """Fallback error when botocore is unavailable."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

router = APIRouter()


class OrderEmailRequest(BaseModel):
    item_details: str
    move_date: str
    phone: str
    locations: str
    estimate_price: float
    stairwells: str
    estimate_calculation_table: str
    email: str
    name: str

    @classmethod
    def _normalize_email(cls, value: str) -> str:
        """Convert spoken-style email addresses into RFC-friendly form."""

        if not value:
            return value

        normalized = value.strip().lower()
        normalized = re.sub(r"\s*(?:\[?at\]?|\(at\)| at )\s*", "@", normalized)
        normalized = re.sub(r"\s*(?:\[?dot\]?|\(dot\)| dot )\s*", ".", normalized)
        normalized = re.sub(r"\s+", "", normalized)
        return normalized

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = cls._normalize_email(value)

        if "@" not in value or value.count("@") != 1:
            raise ValueError("email must contain a single '@' symbol")
        local, domain = value.split("@", 1)
        if not local or not domain or "." not in domain:
            raise ValueError("email must include a domain")
        return value


class EmailConfig(BaseModel):
    region: str
    sender: str
    recipients: List[str]
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    @classmethod
    def from_env(cls) -> "EmailConfig":
        """Load configuration for sending order emails via Amazon SES."""

        recipients_raw = os.getenv("ORDER_EMAIL_RECIPIENTS", "")
        recipients = [addr.strip() for addr in recipients_raw.split(",") if addr.strip()]
        if not recipients:
            raise HTTPException(
                status_code=500,
                detail="ORDER_EMAIL_RECIPIENTS is not configured.",
            )

        sender = os.getenv("ORDER_EMAIL_SENDER") or os.getenv("FROM_EMAIL")
        if not sender:
            raise HTTPException(
                status_code=500,
                detail="ORDER_EMAIL_SENDER or FROM_EMAIL is required for SES emails.",
            )

        region = os.getenv("ORDER_EMAIL_AWS_REGION") or os.getenv("AWS_REGION")
        if not region:
            raise HTTPException(
                status_code=500,
                detail="AWS_REGION or ORDER_EMAIL_AWS_REGION is required for SES emails.",
            )

        access_key = os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

        return cls(
            region=region,
            sender=sender,
            recipients=recipients,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )


def _build_email_body(payload: OrderEmailRequest) -> tuple[str, str]:
    subject = f"New move lead from {payload.name}"
    body = (
        "New move lead received.\n\n"
        f"Caller: {payload.name}\n"
        f"Email: {payload.email}\n"
        f"Phone: {payload.phone}\n\n"
        f"Move date: {payload.move_date}\n"
        f"Locations: {payload.locations}\n"
        f"Stairwells / elevators: {payload.stairwells}\n"
        f"Estimate price: ${payload.estimate_price:,.2f}\n\n"
        "Inventory provided:\n"
        f"{payload.item_details}\n\n"
        "Full calculation:\n"
        f"{payload.estimate_calculation_table}\n"
    )
    return subject, body


def _send_email(config: EmailConfig, subject: str, body: str, reply_to: str) -> None:
    """
    Send the order email using Amazon SES.

    Uses the AWS region and credentials from EmailConfig. If credentials are not
    provided explicitly, boto3 will fall back to its default credential chain.
    """
    if boto3 is None:  # pragma: no cover
        raise HTTPException(
            status_code=500,
            detail="boto3 is not installed; cannot send email via Amazon SES.",
        )

    try:
        ses = boto3.client(
            "ses",
            region_name=config.region,
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
        )

        ses.send_email(
            Source=config.sender,
            Destination={"ToAddresses": config.recipients},
            ReplyToAddresses=[reply_to],
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {
                        "Data": body,
                        "Charset": "UTF-8",
                    }
                },
            },
        )
    except (BotoCoreError, ClientError) as exc:  # pragma: no cover
        raise HTTPException(
            status_code=502,
            detail=f"Failed to send email via SES: {exc}",
        ) from exc


class OrderSMSRequest(BaseModel):
    phone: str
    message: str
    name: str


class SMSConfig(BaseModel):
    region: str
    sender_id: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    @classmethod
    def from_env(cls) -> "SMSConfig":
        region = os.getenv("ORDER_SMS_AWS_REGION") or os.getenv("AWS_REGION") or ""
        if not region:
            raise HTTPException(status_code=500, detail="ORDER_SMS_AWS_REGION or AWS_REGION is required.")

        sender_id = os.getenv("ORDER_SMS_SENDER_ID")
        access_key = os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

        return cls(region=region, sender_id=sender_id, aws_access_key_id=access_key, aws_secret_access_key=secret_key)


def _send_sms(config: SMSConfig, payload: OrderSMSRequest) -> None:
    if boto3 is None:  # pragma: no cover - safety for missing dependency
        raise HTTPException(status_code=500, detail="boto3 is not installed; cannot send SMS.")

    attributes: Dict[str, Any] = {}
    if config.sender_id:
        attributes["AWS.SNS.SMS.SenderID"] = {"DataType": "String", "StringValue": config.sender_id}

    try:
        sns = boto3.client(
            "sns",
            region_name=config.region,
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
        )
        publish_kwargs: Dict[str, Any] = {"PhoneNumber": payload.phone, "Message": payload.message}
        if attributes:
            publish_kwargs["MessageAttributes"] = attributes
        sns.publish(**publish_kwargs)
    except (BotoCoreError, ClientError) as exc:  # pragma: no cover - network errors are environment-specific
        raise HTTPException(status_code=502, detail=f"Failed to send SMS: {exc}") from exc


@router.post("/orders/email")
async def email_order(payload: OrderEmailRequest):
    config = EmailConfig.from_env()
    subject, body = _build_email_body(payload)
    _send_email(config, subject=subject, body=body, reply_to=payload.email)
    return {"status": "sent", "recipients": config.recipients}


@router.post("/orders/sms")
async def sms_order(payload: OrderSMSRequest):
    config = SMSConfig.from_env()
    _send_sms(config, payload)
    return {"status": "sent", "phone": payload.phone}
