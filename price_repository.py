"""Real, Postgres-backed implementation of `calculator.py`'s `PriceRepositoryProtocol`.

Why this is synchronous despite an async DB stack: `EstimateCalculator.calculate()` calls
repository methods many times while building one estimate (per line item, per tier, per
factor) and is itself a pure/sync, CPU-only class - there is no reason to make it `async def`
just because the data originally lives in Postgres. Prices/factors also change rarely (an
admin edits them occasionally), so this repository loads the *entire* catalog once via one
async round-trip (`load_price_repository`) into a small in-memory snapshot, then serves every
lookup for that request purely from memory - no N+1 queries per estimate, no async/sync
boundary inside `calculator.py`. Re-call `load_price_repository` (e.g. on a short TTL, or per
request) to pick up catalog edits.

Fail-fast policy: missing *pricing* data (labor rate, material price, tax rate, risk baseline,
contractor profile) raises `PriceNotFoundError` rather than silently substituting a guessed
number - a quietly-wrong low estimate is how a contractor ends up underwater mid-project. Missing
*factor* data (waste/complexity/accessories/logistics/seasonal) safely defaults to "no effect"
(0 / 1.0 / no rows), because those are additive refinements, not the base price itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from calculator import AccessoryRule
from db.models import (
    AccessoryRuleModel,
    ComplexityRule,
    ContractorProfileModel,
    FixedOverhead,
    LaborRate,
    LogisticsFactor,
    MaterialPrice,
    RiskBaseline,
    SeasonalFactor,
    TaxRate,
    WasteFactor,
)
from schema import ContractorProfile, MaterialTier, PaymentMilestone, PrecisionLevelEnum, WarrantyTerm, WorkItem

# Area threshold (cm^2) above which a tile is considered "large-format" - matches the
# 120x60cm example from master prompt v1 section 4.
LARGE_FORMAT_TILE_AREA_CM2 = 120 * 60


class PriceNotFoundError(RuntimeError):
    """Raised when Ground Truth pricing data required to build an estimate is missing from the
    catalog. Never caught to substitute a guessed price - surface it, fix the catalog."""


@dataclass
class PriceRepository:
    """In-memory snapshot of the price catalog, sourced from Postgres via `load_price_repository`.
    Implements `calculator.PriceRepositoryProtocol` structurally (no explicit inheritance needed
    - `Protocol` is duck-typed)."""

    labor_rates: dict[str, Decimal] = field(default_factory=dict)
    labor_paces: dict[str, Decimal] = field(default_factory=dict)
    material_prices: dict[tuple[str, str | None, MaterialTier], Decimal] = field(default_factory=dict)
    waste_factors: dict[tuple[str, str | None], Decimal] = field(default_factory=dict)
    complexity_rules: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    accessory_rules: dict[str, list[AccessoryRule]] = field(default_factory=dict)
    logistics_factors: dict[tuple[int, bool], Decimal] = field(default_factory=dict)
    seasonal_factors: dict[int, Decimal] = field(default_factory=dict)
    fixed_overheads: dict[str, Decimal] = field(default_factory=dict)
    risk_baselines: dict[PrecisionLevelEnum, Decimal] = field(default_factory=dict)
    tax_rate_value: Decimal | None = None
    contractor_profile_value: ContractorProfile | None = None

    def get_labor_rate(self, work_type: str) -> Decimal:
        try:
            return self.labor_rates[work_type]
        except KeyError as exc:
            raise PriceNotFoundError(f"No labor rate configured for work_type={work_type!r}") from exc

    def get_work_duration_days(self, work_type: str, quantity: Decimal) -> int:
        if quantity <= 0:
            return 1
        pace = self.labor_paces.get(work_type, Decimal("5"))
        if pace <= 0:
            return 1
        return max(1, int((quantity / pace).to_integral_value(rounding=ROUND_CEILING)))

    def get_complexity_factor(self, work_type: str, work_item: WorkItem, is_old_building: bool) -> Decimal:
        factor = Decimal("1")
        if work_item.tile_size_cm:
            try:
                w_str, h_str = work_item.tile_size_cm.lower().split("x")
                if int(w_str) * int(h_str) > LARGE_FORMAT_TILE_AREA_CM2:
                    factor *= self.complexity_rules.get((work_type, "large_format_tile"), Decimal("1"))
            except ValueError:
                pass
        if is_old_building:
            factor *= self.complexity_rules.get((work_type, "old_building"), Decimal("1"))
        return factor

    def get_waste_factor(self, work_type: str, layout_pattern: str | None) -> Decimal:
        return self.waste_factors.get((work_type, layout_pattern), Decimal("0"))

    def get_material_unit_price(self, work_type: str, material: str | None, tier: MaterialTier) -> Decimal:
        for key in ((work_type, material, tier), (work_type, None, tier), (work_type, None, MaterialTier.STANDARD)):
            if key in self.material_prices:
                return self.material_prices[key]
        raise PriceNotFoundError(
            f"No material price configured for work_type={work_type!r}, material={material!r}, tier={tier!r}"
        )

    def get_accessory_rules(self, work_type: str) -> list[AccessoryRule]:
        return self.accessory_rules.get(work_type, [])

    def get_logistics_surcharge_pct(self, floor_number: int | None, has_elevator: bool | None) -> Decimal:
        if floor_number is None:
            return Decimal("0")
        elevator = bool(has_elevator)
        return self.logistics_factors.get((floor_number, elevator), Decimal("0"))

    def get_seasonal_demand_multiplier(self, month: int) -> Decimal:
        return self.seasonal_factors.get(month, Decimal("1.0"))

    def get_fixed_overheads(self) -> dict[str, Decimal]:
        return dict(self.fixed_overheads)

    def get_tax_rate(self) -> Decimal:
        if self.tax_rate_value is None:
            raise PriceNotFoundError("No tax rate configured (expected one row in tax_rates)")
        return self.tax_rate_value

    def get_risk_baseline(self, precision_level: PrecisionLevelEnum) -> Decimal:
        try:
            return self.risk_baselines[precision_level]
        except KeyError as exc:
            raise PriceNotFoundError(f"No risk baseline configured for precision_level={precision_level!r}") from exc

    def get_contractor_profile(self) -> ContractorProfile:
        if self.contractor_profile_value is None:
            raise PriceNotFoundError("No contractor profile configured (expected one row in contractor_profiles)")
        return self.contractor_profile_value


async def load_price_repository(session: AsyncSession) -> PriceRepository:
    """Fetch the entire price catalog in one async pass and build an in-memory `PriceRepository`
    snapshot. Call this once per request (or on a short TTL cache) - see module docstring."""
    repo = PriceRepository()

    for row in (await session.execute(select(LaborRate))).scalars():
        repo.labor_rates[row.work_type] = row.rate
        repo.labor_paces[row.work_type] = row.pace_units_per_day

    for row in (await session.execute(select(MaterialPrice))).scalars():
        repo.material_prices[(row.work_type, row.material, MaterialTier(row.tier))] = row.price

    for row in (await session.execute(select(WasteFactor))).scalars():
        repo.waste_factors[(row.work_type, row.layout_pattern)] = row.factor

    for row in (await session.execute(select(ComplexityRule))).scalars():
        repo.complexity_rules[(row.work_type, row.condition)] = row.multiplier

    for row in (await session.execute(select(AccessoryRuleModel))).scalars():
        repo.accessory_rules.setdefault(row.base_work_type, []).append(
            AccessoryRule(row.accessory_work_type, row.quantity_per_base_unit, row.unit)
        )

    for row in (await session.execute(select(LogisticsFactor))).scalars():
        repo.logistics_factors[(row.floor_number, row.has_elevator)] = row.surcharge_pct

    for row in (await session.execute(select(SeasonalFactor))).scalars():
        repo.seasonal_factors[row.month] = row.demand_multiplier

    for row in (await session.execute(select(FixedOverhead))).scalars():
        repo.fixed_overheads[row.key] = row.amount

    for row in (await session.execute(select(RiskBaseline))).scalars():
        repo.risk_baselines[PrecisionLevelEnum(row.precision_level)] = row.baseline

    tax_row = (await session.execute(select(TaxRate))).scalars().first()
    if tax_row is not None:
        repo.tax_rate_value = tax_row.rate

    contractor_query = select(ContractorProfileModel).options(
        selectinload(ContractorProfileModel.payment_milestones),
        selectinload(ContractorProfileModel.warranty_terms),
    )
    contractor_row = (await session.execute(contractor_query)).scalars().first()
    if contractor_row is not None:
        repo.contractor_profile_value = ContractorProfile(
            company_name=contractor_row.company_name,
            payment_schedule=[
                PaymentMilestone(label=m.label, percent=m.percent, trigger=m.trigger)
                for m in contractor_row.payment_milestones
            ],
            warranty_terms=[
                WarrantyTerm(work_category=w.work_category, warranty_months=w.warranty_months)
                for w in contractor_row.warranty_terms
            ],
        )

    return repo
