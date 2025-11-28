from __future__ import annotations

import os
import tomllib
from pathlib import Path
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

BASE_DIR = Path(__file__).resolve().parent.parent

router = APIRouter()

DEFAULT_SECRET_PATHS = [
    Path("/etc/secrets/secrets.toml"),
    Path("/etc/secrets.toml"),
    BASE_DIR / "secrets.toml",
]


def _load_secrets(path_override: Path | None = None) -> dict:
    candidates = [path_override] if path_override else DEFAULT_SECRET_PATHS
    for path in candidates:
        if path and path.exists():
            with path.open("rb") as fh:
                payload = tomllib.load(fh)
            return payload.get("secrets", payload)
    return {}


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

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
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
    def from_env_or_secrets(cls, *, path_override: Path | None = None) -> "EmailConfig":
        """
        Load configuration for sending order emails via Amazon SES.

        Priority order:
        - Environment variables (Render)
        - Optional secrets.toml mounted into the container
        """
        secrets = _load_secrets(path_override)

        # 1) Recipients: comma-separated list
        recipients_raw = os.getenv("ORDER_EMAIL_RECIPIENTS") or secrets.get("ORDER_EMAIL_RECIPIENTS")
        recipients: List[str] = []
        if isinstance(recipients_raw, str):
            recipients = [addr.strip() for addr in recipients_raw.split(",") if addr.strip()]
        elif isinstance(recipients_raw, list):
            recipients = [str(addr).strip() for addr in recipients_raw if str(addr).strip()]
        if not recipients:
            raise HTTPException(
                status_code=500,
                detail="ORDER_EMAIL_RECIPIENTS is not configured.",
            )

        # 2) Sender: ORDER_EMAIL_SENDER or FROM_EMAIL
        sender = (
            os.getenv("ORDER_EMAIL_SENDER")
            or os.getenv("FROM_EMAIL")
            or secrets.get("ORDER_EMAIL_SENDER")
            or secrets.get("FROM_EMAIL")
        )
        if not sender:
            raise HTTPException(
                status_code=500,
                detail="ORDER_EMAIL_SENDER or FROM_EMAIL is required for SES emails.",
            )

        # 3) AWS region
        region = (
            os.getenv("ORDER_EMAIL_AWS_REGION")
            or os.getenv("AWS_REGION")
            or secrets.get("ORDER_EMAIL_AWS_REGION")
            or secrets.get("AWS_REGION")
        )
        if not region:
            raise HTTPException(
                status_code=500,
                detail="AWS_REGION or ORDER_EMAIL_AWS_REGION is required for SES emails.",
            )

        # 4) AWS credentials (optional if instance profile is used,
        # but we wire them from env in this project)
        access_key = os.getenv("AWS_ACCESS_KEY_ID") or secrets.get("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY") or secrets.get("AWS_SECRET_ACCESS_KEY")

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
    def from_env_or_secrets(cls, *, path_override: Path | None = None) -> "SMSConfig":
        secrets = _load_secrets(path_override)

        region = os.getenv("ORDER_SMS_AWS_REGION") or os.getenv("AWS_REGION") or secrets.get("ORDER_SMS_AWS_REGION") or secrets.get("AWS_REGION") or ""
        if not region:
            raise HTTPException(status_code=500, detail="ORDER_SMS_AWS_REGION or AWS_REGION is required.")

        sender_id = os.getenv("ORDER_SMS_SENDER_ID") or secrets.get("ORDER_SMS_SENDER_ID")
        access_key = os.getenv("AWS_ACCESS_KEY_ID") or secrets.get("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY") or secrets.get("AWS_SECRET_ACCESS_KEY")

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
    config = EmailConfig.from_env_or_secrets()
    subject, body = _build_email_body(payload)
    _send_email(config, subject=subject, body=body, reply_to=payload.email)
    return {"status": "sent", "recipients": config.recipients}


@router.post("/orders/sms")
async def sms_order(payload: OrderSMSRequest):
    config = SMSConfig.from_env_or_secrets()
    _send_sms(config, payload)
    return {"status": "sent", "phone": payload.phone}
