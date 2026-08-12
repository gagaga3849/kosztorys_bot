"""SQLAlchemy 2.x async ORM models - the real Ground Truth price catalog.

This is the durable storage backing `PriceRepositoryProtocol` (defined in `calculator.py`).
`price_repository.py` reads these tables (once, async) into an in-memory snapshot that then
answers `EstimateCalculator`'s synchronous lookups - see that file's module docstring for why.

Design notes:
- Postgres-first (master prompt v2 section 5 + production plan): async engine, `asyncpg` driver.
- Every price/factor a contractor might reasonably want to tune lives in a table, not in
  `calculator.py` or an LLM prompt - so changing prices never requires a code deploy.
- Money and multipliers are `Numeric`, never `Float` - avoids binary floating-point rounding
  errors leaking into estimates (same discipline as `calculator.py`'s `Decimal` usage).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class LaborRate(Base):
    """Unit labor rate for a work type, e.g. 'tiling_floor' -> 80.00 PLN/m2."""

    __tablename__ = "labor_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_type: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    # Contractor's typical pace, used to estimate active work duration (curing time is separate,
    # tracked per-WorkItem in schema.py, not here).
    pace_units_per_day: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("5"))


class MaterialPrice(Base):
    """Material unit price for a work type, at a given tier. `material` is optional: NULL means
    "the default material price for this work_type/tier" (used when the client didn't name a
    specific product); a non-NULL row lets the catalog override the price for a named material.
    """

    __tablename__ = "material_prices"
    __table_args__ = (UniqueConstraint("work_type", "material", "tier", name="uq_material_price"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    work_type: Mapped[str] = mapped_column(String(100), index=True)
    material: Mapped[str | None] = mapped_column(String(200), default=None)
    tier: Mapped[str] = mapped_column(String(20))  # MaterialTier value: economy/standard/premium
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class WasteFactor(Base):
    """e.g. tiling_floor + diagonal layout -> +0.15 waste."""

    __tablename__ = "waste_factors"
    __table_args__ = (UniqueConstraint("work_type", "layout_pattern", name="uq_waste_factor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    work_type: Mapped[str] = mapped_column(String(100), index=True)
    layout_pattern: Mapped[str | None] = mapped_column(String(50), default=None)
    factor: Mapped[Decimal] = mapped_column(Numeric(5, 4))


class ComplexityRule(Base):
    """Generalizes the complexity-factor cases from master prompt v1 section 4:
    large-format tile (>120x60cm) -> 1.3x; demolition in a pre-1970 building -> 1.2x.

    `condition` is a small fixed vocabulary (`"large_format_tile"`, `"old_building"`) evaluated
    in `price_repository.py`; new conditions require a code change there, but the *multiplier*
    and *which work_type it applies to* are fully data-driven.
    """

    __tablename__ = "complexity_rules"
    __table_args__ = (UniqueConstraint("work_type", "condition", name="uq_complexity_rule"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    work_type: Mapped[str] = mapped_column(String(100), index=True)
    condition: Mapped[str] = mapped_column(String(50))
    multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 4))


class AccessoryRuleModel(Base):
    """e.g. 1 m2 of tiling_floor needs 5 kg of tile_adhesive - auto-derived consumables
    (master prompt v1 section 4)."""

    __tablename__ = "accessory_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    base_work_type: Mapped[str] = mapped_column(String(100), index=True)
    accessory_work_type: Mapped[str] = mapped_column(String(100))
    quantity_per_base_unit: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    unit: Mapped[str] = mapped_column(String(20))


class LogisticsFactor(Base):
    """Master prompt v2 section 5, verbatim: surcharge for carrying materials/tools up floors
    without an elevator. 0% for 1-2 floors with an elevator, up to 15% for a 5th floor without.
    """

    __tablename__ = "logistics_factors"
    __table_args__ = (UniqueConstraint("floor_number", "has_elevator", name="uq_logistics_factor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    floor_number: Mapped[int]
    has_elevator: Mapped[bool]
    surcharge_pct: Mapped[Decimal] = mapped_column(Numeric(5, 4))


class SeasonalFactor(Base):
    """Master prompt v2 section 5, verbatim: `wet_process_allowed` flags whether wet trades
    (screed/plaster) can run in an unheated site that month; `demand_multiplier` is the
    high-season labor surcharge. NOTE (Foreman's Suggestion #4 in docs/DIARY.md):
    `wet_process_allowed` is not yet consumed by `calculator.py`'s phase scheduler - only
    `demand_multiplier` is applied today. Wiring the scheduling gate is a follow-up.
    """

    __tablename__ = "seasonal_factors"

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[int] = mapped_column(unique=True)  # 1-12
    wet_process_allowed: Mapped[bool] = mapped_column(default=True)
    demand_multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1.0"))


class FixedOverhead(Base):
    """e.g. 'site_visit' -> 100.00, 'waste_removal' -> 300.00, 'delivery' -> 150.00."""

    __tablename__ = "fixed_overheads"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class RiskBaseline(Base):
    """Baseline risk_coefficient per `PrecisionLevelEnum` (master prompt v2 section 2), before
    the +0.05 old-building / +0.05 hidden-conditions addons applied in `calculator.py`."""

    __tablename__ = "risk_baselines"

    id: Mapped[int] = mapped_column(primary_key=True)
    precision_level: Mapped[str] = mapped_column(String(20), unique=True)
    baseline: Mapped[Decimal] = mapped_column(Numeric(5, 4))


class TaxRate(Base):
    """Single active VAT rate. Modeled as a table (not a constant) so the rate can be updated
    without a deploy if Polish VAT law changes; `key='default'` is the only row expected in v1.
    """

    __tablename__ = "tax_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, default="default")
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))


class ContractorProfileModel(Base):
    """Single-contractor profile for v1 (multi-tenant contractors is a future extension, not
    needed for the Telegram-only v1 Definition of Done)."""

    __tablename__ = "contractor_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(200))

    payment_milestones: Mapped[list["PaymentMilestoneModel"]] = relationship(
        back_populates="contractor", order_by="PaymentMilestoneModel.order_index", cascade="all, delete-orphan"
    )
    warranty_terms: Mapped[list["WarrantyTermModel"]] = relationship(
        back_populates="contractor", cascade="all, delete-orphan"
    )


class PaymentMilestoneModel(Base):
    """e.g. 30% on signing / 40% after rough-in / 30% on completion."""

    __tablename__ = "payment_milestones"

    id: Mapped[int] = mapped_column(primary_key=True)
    contractor_id: Mapped[int] = mapped_column(ForeignKey("contractor_profiles.id"))
    label: Mapped[str] = mapped_column(String(100))
    percent: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    trigger: Mapped[str] = mapped_column(String(200))
    order_index: Mapped[int] = mapped_column(default=0)

    contractor: Mapped[ContractorProfileModel] = relationship(back_populates="payment_milestones")


class WarrantyTermModel(Base):
    """Per-trade warranty period, e.g. tiling -> 24 months, plumbing -> 24 months, electrical ->
    60 months. Deliberately data-driven (master prompt v2 section 7): different trades have
    different statutory/customary warranty norms in Poland - never hardcode this in an LLM prompt.
    """

    __tablename__ = "warranty_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    contractor_id: Mapped[int] = mapped_column(ForeignKey("contractor_profiles.id"))
    work_category: Mapped[str] = mapped_column(String(100))
    warranty_months: Mapped[int]

    contractor: Mapped[ContractorProfileModel] = relationship(back_populates="warranty_terms")
