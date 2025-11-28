from __future__ import annotations

import os
import smtplib
import tomllib
from email.message import EmailMessage
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
    host: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    use_tls: bool = True
    sender: str
    recipients: List[str]

    @classmethod
    def from_env_or_secrets(cls, *, path_override: Path | None = None) -> "EmailConfig":
        secrets = _load_secrets(path_override)

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

        host = os.getenv("ORDER_EMAIL_SMTP_HOST") or secrets.get("ORDER_EMAIL_SMTP_HOST") or ""
        if not host:
            raise HTTPException(status_code=500, detail="ORDER_EMAIL_SMTP_HOST is required.")

        port_raw = os.getenv("ORDER_EMAIL_SMTP_PORT") or secrets.get("ORDER_EMAIL_SMTP_PORT") or 587
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=500, detail="ORDER_EMAIL_SMTP_PORT must be an integer.")

        username = os.getenv("ORDER_EMAIL_SMTP_USERNAME") or secrets.get("ORDER_EMAIL_SMTP_USERNAME")
        password = os.getenv("ORDER_EMAIL_SMTP_PASSWORD") or secrets.get("ORDER_EMAIL_SMTP_PASSWORD")
        use_tls_raw = os.getenv("ORDER_EMAIL_SMTP_USE_TLS") or secrets.get("ORDER_EMAIL_SMTP_USE_TLS")
        use_tls = True if use_tls_raw is None else str(use_tls_raw).lower() not in {"0", "false", "no"}

        sender = (
            os.getenv("ORDER_EMAIL_SENDER")
            or secrets.get("ORDER_EMAIL_SENDER")
            or username
            or recipients[0]
        )

        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            use_tls=use_tls,
            sender=sender,
            recipients=recipients,
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
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message["Reply-To"] = reply_to
    message.set_content(body)

    try:
        with smtplib.SMTP(config.host, config.port, timeout=10) as smtp:
            if config.use_tls:
                smtp.starttls()
            if config.username:
                smtp.login(config.username, config.password or "")
            smtp.send_message(message)
    except Exception as exc:  # pragma: no cover - network errors are environment-specific
        raise HTTPException(status_code=502, detail=f"Failed to send email: {exc}") from exc


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
