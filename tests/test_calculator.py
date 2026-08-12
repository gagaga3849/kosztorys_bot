"""Regression tests for `calculator.py` - the golden-value anchors for the pricing engine.

Uses a small in-memory `FakePriceRepository` (no DB dependency) implementing
`PriceRepositoryProtocol`, so these tests exercise `EstimateCalculator` in complete
isolation. If a future change to `calculator.py` alters these numbers unintentionally, one
of these tests should fail - if the change is deliberate (new formula, new coefficient),
update the expected values here in the same PR and say why (see docs/DIARY.md).
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

import pytest

from calculator import AccessoryRule, EstimateCalculator, compute_wall_area
from schema import (
    ContractorProfile,
    DesignServiceRequest,
    DesignServiceType,
    ExtractedRenovationData,
    MaterialTier,
    PaymentMilestone,
    PrecisionLevelEnum,
    WarrantyTerm,
    WorkItem,
    WorkPhase,
)


class FakePriceRepository:
    """Deterministic in-memory stand-in for the real Postgres-backed `PriceRepository`."""

    def __init__(self) -> None:
        self.labor_rates = {
            "demolition_tiling": Decimal("40"),
            "screed": Decimal("60"),
            "tiling_floor": Decimal("80"),
            "tiling_wall": Decimal("70"),
            "electrical_point": Decimal("100"),
            "bathroom_renovation_generic": Decimal("500"),
        }
        self.material_prices = {
            ("tiling_floor", MaterialTier.ECONOMY): Decimal("40"),
            ("tiling_floor", MaterialTier.STANDARD): Decimal("70"),
            ("tiling_floor", MaterialTier.PREMIUM): Decimal("150"),
            ("tiling_wall", MaterialTier.ECONOMY): Decimal("35"),
            ("tiling_wall", MaterialTier.STANDARD): Decimal("60"),
            ("tiling_wall", MaterialTier.PREMIUM): Decimal("130"),
            ("bathroom_renovation_generic", MaterialTier.STANDARD): Decimal("200"),
            ("screed", MaterialTier.STANDARD): Decimal("25"),
            ("electrical_point", MaterialTier.STANDARD): Decimal("30"),
            ("tile_adhesive", MaterialTier.STANDARD): Decimal("3"),
        }
        self.waste_factors = {
            ("tiling_floor", "straight"): Decimal("0.10"),
            ("tiling_floor", "diagonal"): Decimal("0.15"),
            ("tiling_wall", "straight"): Decimal("0.10"),
            ("tiling_wall", "diagonal"): Decimal("0.15"),
        }
        self.accessory_rules = {
            "tiling_floor": [AccessoryRule("tile_adhesive", Decimal("5"), "kg")],
        }
        self.risk_baselines = {
            PrecisionLevelEnum.LOW: Decimal("0.225"),
            PrecisionLevelEnum.MID: Decimal("0.135"),
            PrecisionLevelEnum.HIGH: Decimal("0.09"),
        }
        self.tax_rate = Decimal("0.23")

    def get_labor_rate(self, work_type: str) -> Decimal:
        return self.labor_rates.get(work_type, Decimal("50"))

    def get_work_duration_days(self, work_type: str, quantity: Decimal) -> int:
        if quantity <= 0:
            return 1
        return max(1, int((quantity / Decimal("5")).to_integral_value(rounding=ROUND_CEILING)))

    def get_complexity_factor(self, work_type: str, work_item: WorkItem, is_old_building: bool) -> Decimal:
        factor = Decimal("1")
        if work_item.tile_size_cm:
            try:
                w_str, h_str = work_item.tile_size_cm.lower().split("x")
                if int(w_str) * int(h_str) > 120 * 60:
                    factor *= Decimal("1.3")
            except ValueError:
                pass
        if work_type == "demolition_tiling" and is_old_building:
            factor *= Decimal("1.2")
        return factor

    def get_waste_factor(self, work_type: str, layout_pattern: str | None) -> Decimal:
        return self.waste_factors.get((work_type, layout_pattern), Decimal("0"))

    def get_material_unit_price(self, work_type: str, material: str | None, tier: MaterialTier) -> Decimal:
        return self.material_prices.get(
            (work_type, tier), self.material_prices.get((work_type, MaterialTier.STANDARD), Decimal("50"))
        )

    def get_accessory_rules(self, work_type: str) -> list[AccessoryRule]:
        return self.accessory_rules.get(work_type, [])

    def get_logistics_surcharge_pct(self, floor_number: int | None, has_elevator: bool | None) -> Decimal:
        if floor_number is None or has_elevator:
            return Decimal("0")
        if floor_number <= 2:
            return Decimal("0")
        return min(Decimal("0.15"), Decimal("0.05") * (floor_number - 2))

    def get_seasonal_demand_multiplier(self, month: int) -> Decimal:
        return Decimal("1.10") if month in (12, 1, 2) else Decimal("1.0")

    def get_fixed_overheads(self) -> dict[str, Decimal]:
        return {"site_visit": Decimal("100"), "waste_removal": Decimal("300"), "delivery": Decimal("150")}

    def get_tax_rate(self) -> Decimal:
        return self.tax_rate

    def get_risk_baseline(self, precision_level: PrecisionLevelEnum) -> Decimal:
        return self.risk_baselines[precision_level]

    def get_contractor_profile(self) -> ContractorProfile:
        return ContractorProfile(
            company_name="Test Renowacje Sp. z o.o.",
            payment_schedule=[PaymentMilestone(label="Advance", percent=Decimal("0.30"), trigger="On signing")],
            warranty_terms=[WarrantyTerm(work_category="tiling", warranty_months=24)],
        )


@pytest.fixture
def prices() -> FakePriceRepository:
    return FakePriceRepository()


# --------------------------------------------------------------------------- #
# compute_wall_area helper
# --------------------------------------------------------------------------- #


def test_compute_wall_area_subtracts_openings() -> None:
    area = compute_wall_area(Decimal("3"), Decimal("4"), Decimal("2.5"), openings_area=Decimal("2"))
    assert area == Decimal("33")  # (3+4)*2*2.5 - 2 = 35 - 2


def test_compute_wall_area_floors_at_zero() -> None:
    area = compute_wall_area(Decimal("1"), Decimal("1"), Decimal("1"), openings_area=Decimal("100"))
    assert area == Decimal("0")


# --------------------------------------------------------------------------- #
# LOW precision
# --------------------------------------------------------------------------- #


def test_low_precision_single_breakdown_uses_widest_risk_buffer(prices: FakePriceRepository) -> None:
    data = ExtractedRenovationData(
        precision_level=PrecisionLevelEnum.LOW,
        work_items=[
            WorkItem(work_type="bathroom_renovation_generic", room="bathroom", quantity=5.0, unit="m2", phase=WorkPhase.FINISH)
        ],
    )
    report = EstimateCalculator(data, prices).calculate()

    assert len(report.cost_breakdowns) == 1
    breakdown = report.cost_breakdowns[0]
    assert breakdown.tier is None
    assert breakdown.risk_coefficient == Decimal("0.225")
    # labor = 5*500 = 2500; material = 5*200 = 1000; overheads = 550; subtotal = 4050
    assert breakdown.subtotal == Decimal("4050.00")


def test_low_precision_has_no_disclaimer(prices: FakePriceRepository) -> None:
    data = ExtractedRenovationData(
        precision_level=PrecisionLevelEnum.LOW,
        work_items=[
            WorkItem(work_type="bathroom_renovation_generic", room="bathroom", quantity=5.0, unit="m2", phase=WorkPhase.FINISH)
        ],
    )
    report = EstimateCalculator(data, prices).calculate()
    assert report.disclaimer is None


# --------------------------------------------------------------------------- #
# MID precision
# --------------------------------------------------------------------------- #


def test_mid_precision_includes_disclaimer_mentioning_missing_fields(prices: FakePriceRepository) -> None:
    data = ExtractedRenovationData(
        precision_level=PrecisionLevelEnum.MID,
        missing_fields=["work_items.material", "work_items.tile_size_cm"],
        work_items=[
            WorkItem(work_type="demolition_tiling", room="bathroom", quantity=5.0, unit="m2", phase=WorkPhase.DEMOLITION),
            WorkItem(
                work_type="tiling_floor",
                room="bathroom",
                quantity=5.0,
                unit="m2",
                layout_pattern="straight",
                phase=WorkPhase.FINISH,
                depends_on=[WorkPhase.DEMOLITION],
            ),
        ],
    )
    report = EstimateCalculator(data, prices).calculate()

    assert len(report.cost_breakdowns) == 1
    assert report.disclaimer is not None
    assert "work_items.material" in report.disclaimer
    assert report.cost_breakdowns[0].risk_coefficient == Decimal("0.135")


# --------------------------------------------------------------------------- #
# HIGH precision
# --------------------------------------------------------------------------- #


def _high_precision_data(**overrides) -> ExtractedRenovationData:
    base = dict(
        precision_level=PrecisionLevelEnum.HIGH,
        total_area_m2=5.0,
        is_old_building=True,
        work_items=[
            WorkItem(
                work_type="demolition_tiling",
                room="bathroom",
                quantity=17.0,
                unit="m2",
                substrate_condition="poor",
                phase=WorkPhase.DEMOLITION,
            ),
            WorkItem(
                work_type="tiling_floor",
                room="bathroom",
                quantity=5.0,
                unit="m2",
                material="ceramic tile",
                tile_size_cm="120x70",  # area 8400cm2 > 120x60 (7200cm2) -> large-format factor
                layout_pattern="diagonal",
                phase=WorkPhase.FINISH,
                depends_on=[WorkPhase.DEMOLITION],
            ),
        ],
    )
    base.update(overrides)
    return ExtractedRenovationData(**base)


def test_high_precision_produces_three_tiers_with_equal_labor_cost(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(), prices).calculate()

    assert [b.tier for b in report.cost_breakdowns] == [
        MaterialTier.ECONOMY,
        MaterialTier.STANDARD,
        MaterialTier.PREMIUM,
    ]
    labor_costs = {b.labor_cost for b in report.cost_breakdowns}
    assert len(labor_costs) == 1, "labor cost must not vary between material tiers"
    material_costs = [b.material_cost for b in report.cost_breakdowns]
    assert material_costs[0] < material_costs[1] < material_costs[2]


def test_high_precision_applies_large_format_tile_complexity_factor(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(), prices).calculate()

    tiling_line = next(li for li in report.line_items if li.work_item.work_type == "tiling_floor")
    assert any(f.factor_type == "complexity" for f in tiling_line.applied_factors)
    # rate 80 * qty 5 * 1.3 = 520
    assert tiling_line.labor_cost == Decimal("520.00")


def test_high_precision_applies_old_building_demolition_complexity_factor(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(), prices).calculate()

    demo_line = next(li for li in report.line_items if li.work_item.work_type == "demolition_tiling")
    # rate 40 * qty 17 * 1.2 (old building) = 816
    assert demo_line.labor_cost == Decimal("816.00")


def test_accessory_line_item_derived_from_base_quantity(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(), prices).calculate()

    adhesive_line = next(li for li in report.line_items if li.work_item.work_type == "tile_adhesive")
    # 5 m2 floor tile * 5 kg/m2 = 25 kg
    assert adhesive_line.quantity_with_waste == Decimal("25.00")
    assert adhesive_line.work_item.notes is not None and "tiling_floor" in adhesive_line.work_item.notes


def test_risk_coefficient_adds_old_building_and_hidden_conditions_addons(prices: FakePriceRepository) -> None:
    report_baseline = EstimateCalculator(
        _high_precision_data(is_old_building=False, hidden_conditions_unknown=False), prices
    ).calculate()
    report_both_flags = EstimateCalculator(
        _high_precision_data(is_old_building=True, hidden_conditions_unknown=True), prices
    ).calculate()

    baseline_risk = report_baseline.cost_breakdowns[0].risk_coefficient
    flagged_risk = report_both_flags.cost_breakdowns[0].risk_coefficient
    assert flagged_risk == baseline_risk + Decimal("0.05") + Decimal("0.05")


def test_exclusions_include_asbestos_warning_for_old_building(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(is_old_building=True), prices).calculate()
    assert any("azbest" in e for e in report.exclusions)


def test_exclusions_omit_old_building_warnings_when_not_flagged(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(is_old_building=False), prices).calculate()
    assert not any("azbest" in e for e in report.exclusions)


def test_phase_schedule_respects_curing_days_and_dependencies(prices: FakePriceRepository) -> None:
    data = _high_precision_data(
        work_items=[
            WorkItem(work_type="demolition_tiling", room="bathroom", quantity=17.0, unit="m2", phase=WorkPhase.DEMOLITION),
            WorkItem(
                work_type="screed",
                room="bathroom",
                quantity=5.0,
                unit="m2",
                phase=WorkPhase.SCREED,
                depends_on=[WorkPhase.DEMOLITION],
                curing_days=28,
            ),
            WorkItem(
                work_type="tiling_floor",
                room="bathroom",
                quantity=5.0,
                unit="m2",
                phase=WorkPhase.FINISH,
                depends_on=[WorkPhase.SCREED],
            ),
        ]
    )
    report = EstimateCalculator(data, prices).calculate()

    by_phase = {p.phase: p for p in report.phase_schedule}
    assert by_phase[WorkPhase.SCREED].starts_after_day == by_phase[WorkPhase.DEMOLITION].ends_on_day
    assert by_phase[WorkPhase.SCREED].curing_days == 28
    assert by_phase[WorkPhase.FINISH].starts_after_day == by_phase[WorkPhase.SCREED].ends_on_day
    assert report.estimated_duration_days == by_phase[WorkPhase.FINISH].ends_on_day


def test_logistics_surcharge_shown_as_project_level_factor(prices: FakePriceRepository) -> None:
    data = _high_precision_data(floor_number=5, has_elevator=False)
    report = EstimateCalculator(data, prices).calculate()

    logistics_factors = [f for f in report.project_level_factors if f.factor_type == "logistics"]
    assert len(logistics_factors) == 1
    assert logistics_factors[0].multiplier == Decimal("0.15")


def test_no_logistics_surcharge_with_elevator(prices: FakePriceRepository) -> None:
    data = _high_precision_data(floor_number=5, has_elevator=True)
    report = EstimateCalculator(data, prices).calculate()
    assert not [f for f in report.project_level_factors if f.factor_type == "logistics"]


def test_seasonal_surcharge_shown_as_project_level_factor(prices: FakePriceRepository) -> None:
    data = _high_precision_data(estimate_month=1)
    report = EstimateCalculator(data, prices).calculate()

    seasonal_factors = [f for f in report.project_level_factors if f.factor_type == "seasonal"]
    assert len(seasonal_factors) == 1
    assert seasonal_factors[0].multiplier == Decimal("0.10")


# --------------------------------------------------------------------------- #
# EXPERT_REQUIRED (heritage handoff)
# --------------------------------------------------------------------------- #


def test_expert_required_produces_no_pricing_at_all(prices: FakePriceRepository) -> None:
    data = ExtractedRenovationData(
        precision_level=PrecisionLevelEnum.EXPERT_REQUIRED,
        is_heritage_site=True,
        heritage_keywords_matched=["zabytek"],
    )
    report = EstimateCalculator(data, prices).calculate()

    assert report.requires_expert_handoff is True
    assert report.cost_breakdowns == []
    assert report.line_items == []
    assert report.phase_schedule == []
    assert report.estimated_duration_days is None
    assert report.expert_handoff_message is not None and "konserwatorem" in report.expert_handoff_message


# --------------------------------------------------------------------------- #
# Design service (v2 section 4) - always separate from construction total
# --------------------------------------------------------------------------- #


def test_design_service_percent_of_budget(prices: FakePriceRepository) -> None:
    data = _high_precision_data(
        design_service=DesignServiceRequest(
            needed=True, service_type=DesignServiceType.PERCENT_OF_BUDGET, fee_percent=Decimal("0.10")
        )
    )
    report = EstimateCalculator(data, prices).calculate()

    # construction_subtotal = labor (816 demolition + 520 tiling) + material (850 + 402.50) = 2588.50
    # design_service_cost = 2588.50 * 0.10 = 258.85, priced independently of cost_breakdowns
    assert report.design_service_cost == Decimal("258.85")
    for breakdown in report.cost_breakdowns:
        assert report.design_service_cost not in (breakdown.subtotal, breakdown.total)


def test_design_service_not_needed_yields_none_cost(prices: FakePriceRepository) -> None:
    data = _high_precision_data(design_service=DesignServiceRequest(needed=False))
    report = EstimateCalculator(data, prices).calculate()
    assert report.design_service_cost is None


def test_design_service_missing_required_field_raises(prices: FakePriceRepository) -> None:
    data = _high_precision_data(
        design_service=DesignServiceRequest(needed=True, service_type=DesignServiceType.FIXED_CONCEPT_FEE)
    )
    with pytest.raises(ValueError):
        EstimateCalculator(data, prices).calculate()
