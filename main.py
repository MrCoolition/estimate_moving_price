from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request, Response
from app.metrics import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from app.catalog import Catalog, MatchResult
from app.observability import (
    hash_items,
    record_alias_hit,
    record_quote_error,
    record_quote_success,
    record_unknown_item,
    span,
    structured_log,
)
from app.packing import PackingCatalog
from app.pricing import (
    ItemAllocation,
    LocationContext,
    PackingRequest,
    QuoteContext,
    QuoteOptions,
    QuoteResult,
    optimize,
)
from app.rules import load_rules
from app.schemas import EstimateRequest, EstimateResponse, detect_box_total, distribute_boxes
from app.security import HMACVerifier, IdempotencyStore

APP_VERSION = datetime.utcnow().strftime("%Y-%m-%d")
BASE_DIR = Path(__file__).parent
CATALOG_PATH = BASE_DIR / "data" / "estimation_weights_volumes_categories.json"
RULES_PATH = BASE_DIR / "data" / "moving_rules.json"
PACKING_PATH = BASE_DIR / "data" / "packing_weight_volume_pricing.tsv"

catalog = Catalog(CATALOG_PATH)
with RULES_PATH.open("r", encoding="utf-8") as fh:
    rules_payload = json.load(fh)
rules = load_rules(RULES_PATH)
packing_catalog = PackingCatalog(tsv_path=PACKING_PATH, json_config=rules_payload["movingQuoterContext"])

hmac_secret = os.getenv("HMAC_SECRET", "")
verifier = HMACVerifier(hmac_secret)
idempotency_store = IdempotencyStore(os.getenv("REDIS_URL"))
allow_internal_debug = os.getenv("ALLOW_INTERNAL_DEBUG", "false").lower() in {"1", "true", "yes"}

api = FastAPI(title="Estimate Moving Price", version=APP_VERSION)


def _hash_file(path: Path) -> str:
    data = path.read_bytes()
    return str(abs(hash(data)))


@api.get("/healthz", include_in_schema=False)
def healthz():
    return {
        "status": "ok",
        "catalog_hash": _hash_file(CATALOG_PATH),
        "rules_hash": _hash_file(RULES_PATH),
        "version": APP_VERSION,
    }


@api.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _resolve_cartons(base: Dict[str, int], additions: Dict[str, int]) -> Dict[str, int]:
    result = dict(base)
    for key, value in additions.items():
        result[key] = result.get(key, 0) + value
    return result


def _match_items(req: EstimateRequest) -> tuple[List[ItemAllocation], bool, List[Dict[str, Any]], List[str], Dict[str, int]]:
    unknowns: List[Dict[str, Any]] = []
    notes: List[str] = []
    allocations: List[ItemAllocation] = []
    needs_clarification = False
    cartons = req.packing.cartons_dict()
    with span("normalize_items"):
        counts = req.items_counter()
    for raw_name, qty in counts.items():
        if qty <= 0:
            continue
        detected_total = detect_box_total(raw_name)
        if detected_total:
            distribution = distribute_boxes(detected_total)
            cartons = _resolve_cartons(cartons, distribution)
            notes.append(
                f"Converted {detected_total} boxes into distribution {distribution} from '{raw_name}'"
            )
            continue
        with span("match_catalog", item=raw_name, quantity=qty):
            match = catalog.match(raw_name)
        if match:
            record_alias_hit(match.approximate)
            allocations.append(ItemAllocation(match=match, quantity=qty))
            continue
        record_unknown_item()
        suggestions = catalog.suggest(raw_name)
        needs_clarification = True
        unknowns.append(
            {
                "name": raw_name,
                "quantity": qty,
                "suggestions": [
                    {
                        "item_id": suggestion.item["id"],
                        "name": suggestion.item["name"],
                        "similarity": round(suggestion.similarity, 4),
                    }
                    for suggestion in suggestions
                ],
            }
        )
    return allocations, needs_clarification, unknowns, notes, cartons


def _location_context(raw: Dict[str, Any]) -> LocationContext:
    access_rule = rules.access_for_location(raw)
    return LocationContext(raw=raw, access_rule=access_rule)


def _build_quote_response(
    req: EstimateRequest,
    allocations: List[ItemAllocation],
    notes: List[str],
    cartons: Dict[str, int],
    needs_clarification: bool,
    unknowns: List[Dict[str, Any]],
    include_trace: bool,
) -> tuple[Dict[str, Any], QuoteResult, int]:
    origin_ctx = _location_context(req.origin.model_dump())
    destination_ctx = _location_context(req.destination.model_dump())
    options = QuoteOptions(
        optimize_for=req.options.optimize_for,
        not_to_exceed=req.options.not_to_exceed,
        seasonality=req.options.seasonality,
    )
    packing_request = PackingRequest(service=req.packing.service, cartons=cartons)
    ctx = QuoteContext(
        move_date=req.move_date,
        distance_miles=float(req.distance_miles),
        origin=origin_ctx,
        destination=destination_ctx,
        allocations=allocations,
        rules=rules,
        packing_catalog=packing_catalog,
        packing_request=packing_request,
        options=options,
        notes=notes,
    )
    with span("optimize"):
        quote, candidates = optimize(ctx)
    final_price = round(quote.total_price, 2)
    labor_cost = round(quote.labor_cost, 2)
    mileage_cost = round(quote.mileage_cost, 2)
    packing_cost = round(quote.packing_cost, 2)
    surcharges = [
        {"type": item["type"], "amount": round(item["amount"], 2)} for item in quote.surcharges
    ]
    discounts = [
        {"type": item["type"], "amount": round(item["amount"], 2)} for item in quote.discounts
    ]
    base_fee = rules.base_fee
    line_items = [
        {"type": "labor", "amount": labor_cost},
        {"type": "mileage", "amount": mileage_cost},
    ]
    if packing_cost:
        line_items.append({"type": "packing", "amount": packing_cost})
    if surcharges:
        for surcharge in surcharges:
            line_items.append({"type": surcharge["type"], "amount": surcharge["amount"]})
    if base_fee:
        line_items.append({"type": "base_fee", "amount": round(base_fee, 2)})
    breakdown_public = {
        "labor_hours_billed": round(quote.billable_hours, 2),
        "movers": quote.movers,
        "trucks": quote.trucks,
        "labor_cost": labor_cost,
        "mileage_cost": mileage_cost,
        "packing_cost": packing_cost,
        "travel_charge_hours": round(quote.travel_hours, 2),
        "surcharges": surcharges,
        "discounts": discounts,
    }
    if notes:
        breakdown_public["notes"] = notes
    if req.packing.service.lower() != "none":
        breakdown_public["cartons"] = cartons
    calc_trace = {
        **quote.calculation_trace,
        "billable_hours": quote.billable_hours,
        "total_weight": ctx.total_weight,
        "total_volume": ctx.total_volume,
    }
    response_payload = {
        "quote_id": f"q_{uuid.uuid4().hex[:10]}",
        "final_price": final_price,
        "currency": "USD",
        "breakdown_public": breakdown_public,
        "line_items": line_items,
        "version": APP_VERSION,
        "needs_clarification": needs_clarification,
        "clarification_items": unknowns if needs_clarification else None,
    }
    if include_trace:
        response_payload["calculation_logic"] = calc_trace
    return response_payload, quote, candidates


@api.post("/estimate", response_model=EstimateResponse)
async def estimate(request: Request):
    raw_body = await request.body()
    verifier.verify(request.headers.get("X-Signature"), raw_body)
    request_start = time.time()
    with span("apply_rules"):
        try:
            payload = EstimateRequest.model_validate_json(raw_body)
        except ValidationError as exc:
            record_quote_error()
            raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc
        except Exception as exc:
            record_quote_error()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    idempotency_key = request.headers.get("Idempotency-Key") or payload.idempotency_key
    debug_header = (request.headers.get("X-Debug") or "").lower() == "true"
    include_trace = allow_internal_debug and debug_header

    def compute() -> Dict[str, Any]:
        allocations, needs_clarification, unknowns, notes, cartons = _match_items(payload)
        if not allocations and not unknowns:
            raise HTTPException(status_code=400, detail="No recognizable items provided")
        response_payload, quote, candidates = _build_quote_response(
            payload, allocations, notes, cartons, needs_clarification, unknowns, include_trace
        )
        latency_ms = (time.time() - request_start) * 1000
        record_quote_success(latency_ms, candidates, quote.movers, quote.trucks)
        structured_log(
            "quote.generated",
            quote_id=response_payload["quote_id"],
            hashed_items=hash_items(payload.items),
            needs_clarification=needs_clarification,
            movers=quote.movers,
            trucks=quote.trucks,
        )
        return response_payload

    try:
        response = idempotency_store.get_or_set(idempotency_key, raw_body, compute)
    except HTTPException:
        record_quote_error()
        raise
    except Exception as exc:  # pragma: no cover
        record_quote_error()
        raise
    return EstimateResponse.model_validate(response)
