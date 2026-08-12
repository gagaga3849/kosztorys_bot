"""Integration regression tests for `price_repository.py` against a real Postgres instance.

Marked `@pytest.mark.integration`. Skipped automatically (see `tests/conftest.py`) if no test
Postgres is reachable - run one locally with:

    docker run --rm -d --name kosztorys_test_pg -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=kosztorys_test -p 55432:5432 postgres:16-alpine

These tests seed the schema with the SAME golden values used in `tests/test_calculator.py`'s
`FakePriceRepository`, so a `PriceRepository` loaded from real Postgres rows behaves identically
to the in-memory fake - proving the DB-backed repository is a faithful `PriceRepositoryProtocol`
implementation, not just "some object with the right method names".
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
from price_repository import PriceNotFoundError, load_price_repository
from schema import MaterialTier, PrecisionLevelEnum, WorkItem, WorkPhase

pytestmark = pytest.mark.integration


async def _seed_full_catalog(session: AsyncSession) -> None:
    session.add_all(
        [
            LaborRate(work_type="tiling_floor", rate=Decimal("80"), pace_units_per_day=Decimal("5")),
            LaborRate(work_type="demolition_tiling", rate=Decimal("40"), pace_units_per_day=Decimal("8")),
            MaterialPrice(work_type="tiling_floor", material=None, tier="economy", price=Decimal("40")),
            MaterialPrice(work_type="tiling_floor", material=None, tier="standard", price=Decimal("70")),
            MaterialPrice(work_type="tiling_floor", material=None, tier="premium", price=Decimal("150")),
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
            RiskBaseline(precision_level="high", baseline=Decimal("0.09")),
            TaxRate(key="default", rate=Decimal("0.23")),
        ]
    )
    contractor = ContractorProfileModel(company_name="Test Renowacje Sp. z o.o.")
    contractor.payment_milestones.append(
        PaymentMilestoneModel(label="Advance", percent=Decimal("0.30"), trigger="On signing", order_index=0)
    )
    contractor.warranty_terms.append(WarrantyTermModel(work_category="tiling", warranty_months=24))
    session.add(contractor)
    await session.commit()


@pytest.mark.asyncio
async def test_load_price_repository_reads_labor_rate(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    assert repo.get_labor_rate("tiling_floor") == Decimal("80")


@pytest.mark.asyncio
async def test_missing_labor_rate_raises_price_not_found(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    with pytest.raises(PriceNotFoundError):
        repo.get_labor_rate("some_unknown_work_type")


@pytest.mark.asyncio
async def test_material_price_falls_back_to_standard_tier_when_tier_missing(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    # tile_adhesive only has a STANDARD row seeded - PREMIUM should fall back to it.
    assert repo.get_material_unit_price("tile_adhesive", None, MaterialTier.PREMIUM) == Decimal("3")


@pytest.mark.asyncio
async def test_missing_material_price_raises_price_not_found(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    with pytest.raises(PriceNotFoundError):
        repo.get_material_unit_price("unknown_work_type", None, MaterialTier.STANDARD)


@pytest.mark.asyncio
async def test_waste_factor_defaults_to_zero_when_missing(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    assert repo.get_waste_factor("tiling_wall", "diagonal") == Decimal("0")


@pytest.mark.asyncio
async def test_complexity_factor_applies_large_format_and_old_building_rules(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    large_tile_item = WorkItem(
        work_type="tiling_floor",
        quantity=5,
        unit="m2",
        tile_size_cm="120x70",
        phase=WorkPhase.FINISH,
    )
    assert repo.get_complexity_factor("tiling_floor", large_tile_item, is_old_building=False) == Decimal("1.3")

    demolition_item = WorkItem(work_type="demolition_tiling", quantity=17, unit="m2", phase=WorkPhase.DEMOLITION)
    assert repo.get_complexity_factor("demolition_tiling", demolition_item, is_old_building=True) == Decimal("1.2")


@pytest.mark.asyncio
async def test_accessory_rules_round_trip(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    rules = repo.get_accessory_rules("tiling_floor")
    assert len(rules) == 1
    assert rules[0].accessory_work_type == "tile_adhesive"
    assert rules[0].quantity_per_base_unit == Decimal("5")


@pytest.mark.asyncio
async def test_logistics_surcharge_exact_match_and_default(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    assert repo.get_logistics_surcharge_pct(5, False) == Decimal("0.15")
    assert repo.get_logistics_surcharge_pct(None, None) == Decimal("0")
    # No row seeded for (3, False) -> safe default of 0, not an error.
    assert repo.get_logistics_surcharge_pct(3, False) == Decimal("0")


@pytest.mark.asyncio
async def test_seasonal_multiplier_lookup_and_default(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    assert repo.get_seasonal_demand_multiplier(1) == Decimal("1.10")
    assert repo.get_seasonal_demand_multiplier(7) == Decimal("1.0")  # not seeded -> default


@pytest.mark.asyncio
async def test_fixed_overheads_returns_all_rows(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    assert repo.get_fixed_overheads() == {"site_visit": Decimal("100"), "waste_removal": Decimal("300")}


@pytest.mark.asyncio
async def test_tax_rate_round_trip(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    assert repo.get_tax_rate() == Decimal("0.23")


@pytest.mark.asyncio
async def test_missing_tax_rate_raises_price_not_found(db_session: AsyncSession) -> None:
    # Seed nothing at all.
    repo = await load_price_repository(db_session)

    with pytest.raises(PriceNotFoundError):
        repo.get_tax_rate()


@pytest.mark.asyncio
async def test_risk_baseline_round_trip_and_missing_raises(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    assert repo.get_risk_baseline(PrecisionLevelEnum.LOW) == Decimal("0.225")
    with pytest.raises(PriceNotFoundError):
        repo.get_risk_baseline(PrecisionLevelEnum.MID)  # not seeded


@pytest.mark.asyncio
async def test_contractor_profile_round_trip_with_milestones_and_warranties(db_session: AsyncSession) -> None:
    await _seed_full_catalog(db_session)

    repo = await load_price_repository(db_session)

    profile = repo.get_contractor_profile()
    assert profile.company_name == "Test Renowacje Sp. z o.o."
    assert profile.payment_schedule[0].label == "Advance"
    assert profile.payment_schedule[0].percent == Decimal("0.30")
    assert profile.warranty_terms[0].work_category == "tiling"
    assert profile.warranty_terms[0].warranty_months == 24


@pytest.mark.asyncio
async def test_missing_contractor_profile_raises_price_not_found(db_session: AsyncSession) -> None:
    repo = await load_price_repository(db_session)

    with pytest.raises(PriceNotFoundError):
        repo.get_contractor_profile()


@pytest.mark.asyncio
async def test_price_repository_end_to_end_matches_calculator(db_session: AsyncSession) -> None:
    """The real integration point: a DB-loaded `PriceRepository` must work as a drop-in
    `PriceRepositoryProtocol` for `EstimateCalculator`, exactly like the in-memory fake does in
    `tests/test_calculator.py`."""
    await _seed_full_catalog(db_session)
    # demolition_tiling needs a material price row too - the calculator prices materials for
    # every work_item, even demolition (skip/debris removal materials).
    db_session.add(MaterialPrice(work_type="demolition_tiling", material=None, tier="standard", price=Decimal("50")))
    await db_session.commit()
    repo = await load_price_repository(db_session)

    from calculator import EstimateCalculator
    from schema import ExtractedRenovationData

    data = ExtractedRenovationData(
        raw_text="demolition + floor tiling",
        country="PL",
        currency="PLN",
        city="Warsaw",
        rooms=["bathroom"],
        total_area_m2=Decimal("5"),
        is_old_building=True,
        work_items=[
            WorkItem(
                work_type="demolition_tiling",
                room="bathroom",
                quantity=17,
                unit="m2",
                phase=WorkPhase.DEMOLITION,
            ),
            WorkItem(
                work_type="tiling_floor",
                room="bathroom",
                quantity=5,
                unit="m2",
                material="ceramic tile",
                tile_size_cm="120x70",
                layout_pattern="diagonal",
                phase=WorkPhase.FINISH,
                depends_on=[WorkPhase.DEMOLITION],
            ),
        ],
        precision_level=PrecisionLevelEnum.HIGH,
    )

    report = EstimateCalculator(data, repo).calculate()

    assert len(report.cost_breakdowns) == 3
    tiling_line = next(li for li in report.line_items if li.work_item.work_type == "tiling_floor")
    assert tiling_line.labor_cost == Decimal("520.00")  # 5 * 80 * 1.3 (large-format complexity)
