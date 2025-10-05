from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from math import ceil
from typing import Dict, List, Optional

from .catalog import CatalogItem, MatchResult
from .packing import PackingCatalog
from .rules import AccessRule, MovingRules


@dataclass
class LocationContext:
    raw: Dict[str, int | float | bool | str]
    access_rule: AccessRule


@dataclass
class ItemAllocation:
    match: MatchResult
    quantity: int

    @property
    def total_weight(self) -> float:
        return self.match.item["weight_lbs"] * self.quantity

    @property
    def total_volume(self) -> float:
        return self.match.item["volume_cuft"] * self.quantity


@dataclass
class PackingRequest:
    service: str
    cartons: Dict[str, int]


@dataclass
class QuoteOptions:
    optimize_for: str = "lowest_price"
    not_to_exceed: bool = False
    seasonality: str = "auto"


@dataclass
class QuoteContext:
    move_date: date
    distance_miles: float
    origin: LocationContext
    destination: LocationContext
    allocations: List[ItemAllocation]
    rules: MovingRules
    packing_catalog: PackingCatalog
    packing_request: PackingRequest
    options: QuoteOptions
    notes: List[str] = field(default_factory=list)

    @property
    def total_weight(self) -> float:
        return sum(item.total_weight for item in self.allocations)

    @property
    def total_volume(self) -> float:
        return sum(item.total_volume for item in self.allocations)


@dataclass
class QuoteResult:
    movers: int
    trucks: int
    billable_hours: float
    labor_cost: float
    mileage_cost: float
    packing_cost: float
    travel_hours: float
    surcharges: List[Dict[str, float]]
    discounts: List[Dict[str, float]]
    total_price: float
    work_hours: float
    packing_time_hours: float
    calculation_trace: Dict[str, float]


def compute_productivity_hours(total_weight: float, movers: int, origin: LocationContext, destination: LocationContext) -> float:
    if movers <= 0:
        return 0.0
    load_hours = total_weight / (movers * origin.access_rule.lbs_per_mover_hour)
    unload_hours = total_weight / (movers * destination.access_rule.lbs_per_mover_hour)
    return load_hours + unload_hours


def compute_site_adjustments_minutes(location: dict, rules: MovingRules) -> float:
    minutes = 0.0
    stairs = max(int(location.get("stairs_flights") or 0), 0)
    minutes += stairs * rules.stairs_minutes_per_flight
    long_carry = max(int(location.get("long_carry_feet") or 0), 0)
    if long_carry > 0 and rules.long_carry_minutes_per_50ft:
        minutes += math.ceil(long_carry / 50) * rules.long_carry_minutes_per_50ft
    if location.get("elevator"):
        minutes += rules.elevator_minutes
    return minutes


def compute_packing(ctx: QuoteContext, mover_hourly_rate: float) -> tuple[float, float]:
    if ctx.packing_request.service.lower() == "none":
        return 0.0, 0.0
    total_cost = 0.0
    total_hours = 0.0
    cp_service = ctx.packing_request.service.upper() == "CP"
    for code, quantity in ctx.packing_request.cartons.items():
        if quantity <= 0:
            continue
        sku = ctx.packing_catalog.get(code)
        if not sku:
            continue
        box_cost = sku.box_rate * quantity
        labor_cost = sku.labor_rate * quantity if cp_service else 0.0
        total_cost += box_cost + labor_cost
        if cp_service and mover_hourly_rate:
            total_hours += labor_cost / mover_hourly_rate
    return total_cost, total_hours


def protective_materials_charge(total_weight: float) -> float:
    if total_weight <= 0:
        return 0.0
    units = math.ceil(total_weight / 1000.0)
    return units * 5.0


def evaluate_candidate(movers: int, trucks: int, ctx: QuoteContext) -> QuoteResult:
    total_weight = ctx.total_weight
    work_hours = compute_productivity_hours(total_weight, movers, ctx.origin, ctx.destination)
    travel_hours = ctx.rules.travel_charge_hours
    if ctx.distance_miles < 30:
        travel_hours += ctx.rules.local_extra_minutes_under_30_miles / 60.0
    else:
        travel_hours += max(ctx.distance_miles / 30.0, 0.5)
    rate_card = ctx.rules.rate_card_for(ctx.move_date, is_local=ctx.distance_miles < 30)
    mover_rate = float(rate_card.mover_rate_per_hour)
    truck_rate = float(rate_card.truck_rate_per_hour)
    adjustment_minutes = compute_site_adjustments_minutes(ctx.origin.raw, ctx.rules) + compute_site_adjustments_minutes(ctx.destination.raw, ctx.rules)
    packing_cost, packing_time_hours = compute_packing(ctx, mover_rate)
    total_hours = work_hours + travel_hours + (adjustment_minutes / 60.0) + packing_time_hours
    billable_hours = max(ctx.rules.min_billable_hours, total_hours)
    labor_hourly = mover_rate * movers + truck_rate * trucks
    labor_cost = labor_hourly * billable_hours
    mileage_cost = ctx.distance_miles * ctx.rules.mileage_rate
    surcharges = []
    protective_charge = protective_materials_charge(total_weight)
    if protective_charge:
        surcharges.append({"type": "protective_materials", "amount": round(protective_charge, 2)})
    discounts: List[Dict[str, float]] = []
    subtotal = labor_cost + mileage_cost + packing_cost + sum(s["amount"] for s in surcharges)
    total_price = subtotal + ctx.rules.base_fee
    if ctx.options.not_to_exceed:
        total_price *= (1 + ctx.rules.nte_buffer_percent)
    return QuoteResult(
        movers=movers,
        trucks=trucks,
        billable_hours=billable_hours,
        labor_cost=labor_cost,
        mileage_cost=mileage_cost,
        packing_cost=packing_cost,
        travel_hours=travel_hours,
        surcharges=surcharges,
        discounts=discounts,
        total_price=total_price,
        work_hours=work_hours,
        packing_time_hours=packing_time_hours,
        calculation_trace={
            "work_hours": work_hours,
            "travel_hours": travel_hours,
            "adjustment_minutes": adjustment_minutes,
            "packing_time_hours": packing_time_hours,
            "labor_hourly": labor_hourly,
        },
    )


def optimize(ctx: QuoteContext) -> tuple[QuoteResult, int]:
    min_movers = ctx.rules.min_movers
    max_movers = ctx.rules.max_movers
    total_weight = ctx.total_weight
    baseline = ctx.rules.baseline_mover_threshold_lbs
    step = ctx.rules.additional_mover_step_lbs
    min_for_weight = min_movers
    if total_weight > baseline:
        extra = total_weight - baseline
        min_for_weight += math.ceil(extra / step)
    min_movers = max(min_movers, min_for_weight)
    min_trucks = max(1, ceil(total_weight / ctx.rules.truck_capacity_lbs))
    best: Optional[QuoteResult] = None
    candidate_count = 0
    max_trucks = max(ctx.rules.max_trucks, min_trucks)
    for movers in range(min_movers, max_movers + 1):
        for trucks in range(min_trucks, max_trucks + 1):
            candidate_count += 1
            quote = evaluate_candidate(movers, trucks, ctx)
            if best is None or quote.total_price < best.total_price:
                best = quote
    if best is None:
        raise ValueError("No valid quote candidates evaluated")
    return best, candidate_count
