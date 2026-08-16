"""Local/dev tool: seed a minimal demo price catalog so a fresh Postgres database (schema
only, via `alembic upgrade head`) has enough data for `EstimateCalculator` to produce a real
estimate for a floor-tiling job - the same golden dataset `tests/test_price_repository.py`
and `tests/test_calculator.py`'s `FakePriceRepository` use, so results match what the test
suite already verifies. Also seeds every work_type in `llm_parser.KNOWN_WORK_TYPES` (the
closed vocabulary the LLM is constrained to - see docs/CHANNEL_STRATEGY_AND_INPUT_ROBUSTNESS.md
sec 3.2) so a live conversation never hits `PriceNotFoundError` for a common trade.

Not part of the master prompt's file order or production Definition of Done - purely a
convenience for local demos/manual testing (e.g. via `scripts/telegram_polling.py`). A real
deployment would populate the price catalog through a proper admin/import process instead.

Usage:
    source .venv/bin/activate
    python scripts/seed_demo_prices.py

Requires `DATABASE_URL` (reads it the same way `db/session.py` does) and an already-migrated
schema (`alembic upgrade head`).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from db.models import (
    AccessoryRuleModel,
    ComplexityRule,
    ContractorProfileModel,
    FixedOverhead,
    LaborRate,
    LogisticsFactor,
    MaterialPrice,
    PaymentMilestoneModel,
    RiskBaseline,
    SeasonalFactor,
    TaxRate,
    WarrantyTermModel,
    WasteFactor,
)
from db.session import create_engine, session_scope


async def main() -> None:
    engine = create_engine()
    try:
        async with session_scope(engine) as session:
            session.add_all(
                [
                    LaborRate(work_type="tiling_floor", rate=Decimal("80"), pace_units_per_day=Decimal("5")),
                    LaborRate(work_type="tiling_wall", rate=Decimal("70"), pace_units_per_day=Decimal("5")),
                    LaborRate(work_type="demolition_tiling", rate=Decimal("40"), pace_units_per_day=Decimal("8")),
                    # The remaining `llm_parser.KNOWN_WORK_TYPES` entries - common trades that
                    # used to crash live conversations with `PriceNotFoundError` before the LLM
                    # was constrained to this closed vocabulary.
                    LaborRate(work_type="demolition", rate=Decimal("35"), pace_units_per_day=Decimal("12")),
                    LaborRate(work_type="screed", rate=Decimal("60"), pace_units_per_day=Decimal("8")),
                    LaborRate(work_type="plastering", rate=Decimal("45"), pace_units_per_day=Decimal("10")),
                    LaborRate(work_type="painting", rate=Decimal("25"), pace_units_per_day=Decimal("15")),
                    LaborRate(work_type="electrical_point", rate=Decimal("100"), pace_units_per_day=Decimal("6")),
                    LaborRate(work_type="plumbing_point", rate=Decimal("120"), pace_units_per_day=Decimal("5")),
                    # Generic LOW-precision fallback (master prompt section 3: "an averaged
                    # price per m2 from the DB, giving a budget range") - used by
                    # `llm_parser.py`'s `_synthesize_generic_work_item` whenever the client's
                    # message is too vague to name a specific trade, so a rough estimate can
                    # still be priced instead of coming back as 0 PLN labor/materials.
                    LaborRate(work_type="renovation_generic", rate=Decimal("400"), pace_units_per_day=Decimal("3")),
                    MaterialPrice(work_type="tiling_floor", material=None, tier="economy", price=Decimal("40")),
                    MaterialPrice(work_type="tiling_floor", material=None, tier="standard", price=Decimal("70")),
                    MaterialPrice(work_type="tiling_floor", material=None, tier="premium", price=Decimal("150")),
                    MaterialPrice(work_type="tiling_wall", material=None, tier="economy", price=Decimal("35")),
                    MaterialPrice(work_type="tiling_wall", material=None, tier="standard", price=Decimal("60")),
                    MaterialPrice(work_type="tiling_wall", material=None, tier="premium", price=Decimal("130")),
                    MaterialPrice(work_type="demolition", material=None, tier="standard", price=Decimal("0")),
                    MaterialPrice(work_type="screed", material=None, tier="standard", price=Decimal("25")),
                    MaterialPrice(work_type="plastering", material=None, tier="standard", price=Decimal("20")),
                    MaterialPrice(work_type="painting", material=None, tier="standard", price=Decimal("15")),
                    MaterialPrice(work_type="electrical_point", material=None, tier="standard", price=Decimal("30")),
                    MaterialPrice(work_type="plumbing_point", material=None, tier="standard", price=Decimal("40")),
                    MaterialPrice(work_type="renovation_generic", material=None, tier="standard", price=Decimal("250")),
                    MaterialPrice(work_type="tile_adhesive", material=None, tier="standard", price=Decimal("3")),
                    WasteFactor(work_type="tiling_floor", layout_pattern="diagonal", factor=Decimal("0.15")),
                    WasteFactor(work_type="tiling_floor", layout_pattern="straight", factor=Decimal("0.10")),
                    ComplexityRule(work_type="tiling_floor", condition="large_format_tile", multiplier=Decimal("1.3")),
                    ComplexityRule(work_type="demolition_tiling", condition="old_building", multiplier=Decimal("1.2")),
                    AccessoryRuleModel(
                        base_work_type="tiling_floor",
                        accessory_work_type="tile_adhesive",
                        quantity_per_base_unit=Decimal("5"),
                        unit="kg",
                    ),
                    LogisticsFactor(floor_number=5, has_elevator=False, surcharge_pct=Decimal("0.15")),
                    LogisticsFactor(floor_number=1, has_elevator=False, surcharge_pct=Decimal("0")),
                    SeasonalFactor(month=1, wet_process_allowed=False, demand_multiplier=Decimal("1.10")),
                    SeasonalFactor(month=6, wet_process_allowed=True, demand_multiplier=Decimal("1.0")),
                    FixedOverhead(key="site_visit", amount=Decimal("100")),
                    FixedOverhead(key="waste_removal", amount=Decimal("300")),
                    RiskBaseline(precision_level="low", baseline=Decimal("0.225")),
                    RiskBaseline(precision_level="mid", baseline=Decimal("0.135")),
                    RiskBaseline(precision_level="high", baseline=Decimal("0.09")),
                    TaxRate(key="default", rate=Decimal("0.23")),
                ]
            )
            contractor = ContractorProfileModel(company_name="Demo Renowacje Sp. z o.o.")
            contractor.payment_milestones.append(
                PaymentMilestoneModel(label="Zaliczka", percent=Decimal("0.30"), trigger="Przy podpisaniu", order_index=0)
            )
            contractor.warranty_terms.append(WarrantyTermModel(work_category="tiling", warranty_months=24))
            session.add(contractor)
            await session.commit()
        print("Demo price catalog seeded (full llm_parser.KNOWN_WORK_TYPES vocabulary + generic fallback).")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
