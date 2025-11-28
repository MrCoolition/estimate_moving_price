from __future__ import annotations

from datetime import date
from pathlib import Path
import os
from typing import Dict, Iterable, List
import smtplib
from email.message import EmailMessage

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .furniture_catalog import FurnitureCatalog
from .quotes import LocationProfile, MoveSpec, compute_quote

BASE_DIR = Path(__file__).resolve().parent.parent
CATALOG_PATH = BASE_DIR / "data" / "estimation_weights_volumes_categories.json"

furniture_catalog = FurnitureCatalog(CATALOG_PATH)
router = APIRouter()


class EstimateRequest(BaseModel):
    distance_miles: float = Field(..., ge=0)
    move_date: date
    items: Dict[str, int]

    @field_validator("items")
    @classmethod
    def validate_items(cls, value: Dict[str, int]) -> Dict[str, int]:
        if not isinstance(value, dict) or not value:
            raise ValueError("items must be a non-empty object")
        normalized: Dict[str, int] = {}
        for key, qty in value.items():
            try:
                quantity = int(qty)
            except (TypeError, ValueError):
                raise ValueError("item quantities must be integers")
            if quantity < 0:
                raise ValueError("item quantities must be non-negative")
            normalized[str(key)] = quantity
        return normalized


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
    sender: str
    recipients: List[str]
    use_tls: bool = True

    @classmethod
    def from_environment(cls) -> "EmailConfig":
        recipients_raw = os.getenv("ORDER_EMAIL_RECIPIENTS", "")
        recipients = [addr.strip() for addr in recipients_raw.split(",") if addr.strip()]
        if not recipients:
            raise HTTPException(
                status_code=500,
                detail="ORDER_EMAIL_RECIPIENTS is not configured. Set a comma-separated list of email addresses.",
            )

        host = os.getenv("ORDER_EMAIL_SMTP_HOST")
        if not host:
            raise HTTPException(status_code=500, detail="ORDER_EMAIL_SMTP_HOST is required to send email.")

        sender = os.getenv("ORDER_EMAIL_SENDER") or os.getenv("ORDER_EMAIL_SMTP_USERNAME") or recipients[0]
        username = os.getenv("ORDER_EMAIL_SMTP_USERNAME")
        password = os.getenv("ORDER_EMAIL_SMTP_PASSWORD")
        port = int(os.getenv("ORDER_EMAIL_SMTP_PORT", "587"))
        use_tls = os.getenv("ORDER_EMAIL_SMTP_USE_TLS", "true").lower() != "false"

        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            sender=sender,
            recipients=recipients,
            use_tls=use_tls,
        )


def _build_email_message(payload: OrderEmailRequest, sender: str, recipients: Iterable[str]) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = f"New move lead from {payload.name}"
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Reply-To"] = payload.email

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
    message.set_content(body)
    return message


def _send_email(config: EmailConfig, message: EmailMessage) -> None:
    try:
        with smtplib.SMTP(config.host, config.port, timeout=20) as smtp:
            if config.use_tls:
                smtp.starttls()
            if config.username and config.password:
                smtp.login(config.username, config.password)
            smtp.send_message(message)
    except Exception as exc:  # pragma: no cover - network errors are environment-specific
        raise HTTPException(status_code=502, detail=f"Failed to send email: {exc}") from exc


@router.post("/orders/email")
async def email_order(payload: OrderEmailRequest):
    config = EmailConfig.from_environment()
    message = _build_email_message(payload, sender=config.sender, recipients=config.recipients)
    _send_email(config, message)
    return {"status": "sent", "recipients": config.recipients}


@router.post("/estimate")
async def create_estimate(payload: EstimateRequest):
    try:
        total_weight_lbs, breakdown = furniture_catalog.total_weight(payload.items)
    except ValueError as exc:  # pragma: no cover - handled by FastAPI validation
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    is_intrastate = payload.distance_miles > 30
    origin_to_destination_minutes = max(20.0, float(payload.distance_miles) * 1.5)
    friday_or_saturday = payload.move_date.weekday() in (4, 5)

    spec = MoveSpec(
        total_weight_lbs=total_weight_lbs,
        location_profile=LocationProfile.MULTI_FLOOR,
        friday_or_saturday=friday_or_saturday,
        is_intrastate=is_intrastate,
        origin_to_destination_minutes=origin_to_destination_minutes,
        distance_miles=float(payload.distance_miles),
    )

    quote = compute_quote(spec)
    quote["inventory_breakdown"] = breakdown
    quote["total_weight_lbs"] = total_weight_lbs
    return quote
