from __future__ import annotations

from datetime import date
from pathlib import Path
import tomllib
from typing import Dict, List

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


DEFAULT_SECRET_PATHS = [
    Path("/etc/secrets/secrets.toml"),
    Path("/etc/secrets.toml"),
    Path.home() / ".streamlit" / "secrets.toml",
    BASE_DIR / "secrets.toml",
]


def _load_secrets(path_override: Path | None = None) -> dict:
    candidates = [path_override] if path_override else DEFAULT_SECRET_PATHS
    for path in candidates:
        if path and path.exists():
            with path.open("rb") as fh:
                payload = tomllib.load(fh)
            return payload.get("secrets", payload)
    raise HTTPException(
        status_code=500,
        detail="secrets.toml not found. Add one to /etc/secrets/, ~/.streamlit/, or the project root.",
    )


class EmailConfig(BaseModel):
    region: str
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    sender: str
    recipients: List[str]

    @classmethod
    def from_secrets(cls, *, path_override: Path | None = None) -> "EmailConfig":
        secrets = _load_secrets(path_override)

        recipients_raw = secrets.get("ORDER_EMAIL_RECIPIENTS")
        recipients: List[str] = []
        if isinstance(recipients_raw, str):
            recipients = [addr.strip() for addr in recipients_raw.split(",") if addr.strip()]
        elif isinstance(recipients_raw, list):
            recipients = [str(addr).strip() for addr in recipients_raw if str(addr).strip()]
        if not recipients:
            raise HTTPException(
                status_code=500,
                detail="ORDER_EMAIL_RECIPIENTS is not configured in secrets.toml.",
            )

        region = secrets.get("ORDER_EMAIL_AWS_REGION") or secrets.get("AWS_REGION") or ""
        if not region:
            raise HTTPException(status_code=500, detail="ORDER_EMAIL_AWS_REGION or AWS_REGION is required in secrets.toml.")

        sender = secrets.get("ORDER_EMAIL_SENDER") or recipients[0]
        access_key = secrets.get("AWS_ACCESS_KEY_ID")
        secret_key = secrets.get("AWS_SECRET_ACCESS_KEY")

        return cls(
            region=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
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
    if boto3 is None:  # pragma: no cover - safety for missing dependency
        raise HTTPException(status_code=500, detail="boto3 is not installed; cannot send email.")

    try:
        ses = boto3.client(
            "ses",
            region_name=config.region,
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
        )
        ses.send_email(
            Source=config.sender,
            Destination={"ToAddresses": list(config.recipients)},
            ReplyToAddresses=[reply_to],
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )
    except (BotoCoreError, ClientError) as exc:  # pragma: no cover - network errors are environment-specific
        raise HTTPException(status_code=502, detail=f"Failed to send email: {exc}") from exc


@router.post("/orders/email")
async def email_order(payload: OrderEmailRequest):
    config = EmailConfig.from_secrets()
    subject, body = _build_email_body(payload)
    _send_email(config, subject=subject, body=body, reply_to=payload.email)
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
