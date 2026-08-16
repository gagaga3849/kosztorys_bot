"""Deterministic pricing engine for the Smart Estimate Bot.

LLM never computes sums here or anywhere else. `EstimateCalculator` is a pure class: given
`ExtractedRenovationData` (facts only, produced by `llm_parser.py`) and a price-repository
implementation (the Ground Truth), it deterministically produces an `EstimateReport`. It has
no side effects (no I/O, no DB writes, no network calls) and is fully unit-testable using a
fake in-memory `PriceRepositoryProtocol` implementation - see `tests/test_calculator.py`.

Formulas implemented (master prompt v1 section 4 + v2 sections 1, 2, 5, 6):

    Labor Cost_i    = Quantity_i * Unit Rate_i * Complexity Factor_i
    Material Cost_i = Base Material Qty_i * (1 + Waste Factor_i) * Unit Price_i(tier)
    Labor Cost      = Sigma(Labor Cost_i) + Logistics Surcharge + Seasonal Surcharge
    Subtotal        = Labor Cost + Material Cost + Fixed Overheads
    Risk_Buffer     = Subtotal * risk_coefficient
    Total Estimate  = (Subtotal + Risk_Buffer) * (1 + Tax Rate)

`risk_coefficient` = repository baseline for the precision level, +0.05 if the building is
pre-1970, +0.05 if the substrate was never opened up/inspected (v2 section 2).

At HIGH precision, `calculate()` produces three `CostBreakdown` entries (economy/standard/
premium) sharing the same labor cost but different material costs (v2 section 6). At
EXPERT_REQUIRED, no price is computed at all - see `_build_expert_required_report`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

from schema import (
    AppliedFactor,
    ContractorProfile,
    CostBreakdown,
    DesignServiceType,
    EstimateLineItem,
    EstimateReport,
    ExtractedRenovationData,
    MaterialTier,
    PhaseScheduleItem,
    PrecisionLevelEnum,
    WorkItem,
    WorkPhase,
)

TWO_PLACES = Decimal("0.01")

# Additive risk-coefficient adjustments (master prompt v2 section 2). The precision-level
# baseline itself is NOT hardcoded here - it is sourced from the price repository so a
# contractor can tune it without a code change.
OLD_BUILDING_RISK_ADDON = Decimal("0.05")
HIDDEN_CONDITIONS_RISK_ADDON = Decimal("0.05")

_HIGH_PRECISION_TIERS: tuple[MaterialTier, ...] = (
    MaterialTier.ECONOMY,
    MaterialTier.STANDARD,
    MaterialTier.PREMIUM,
)


def money(value: Decimal) -> Decimal:
    """Round a Decimal to 2 places using standard half-up rounding. The only place monetary
    rounding happens - all intermediate arithmetic stays at full Decimal precision."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def compute_wall_area(a: Decimal, b: Decimal, h: Decimal, openings_area: Decimal = Decimal("0")) -> Decimal:
    """Wall area formula from master prompt v1 section 4: S = (A + B) * 2 * H - S_openings.

    `a`, `b` are the room's two side lengths (m), `h` is wall height (m), `openings_area` is
    the total area (m2) of windows/doors to subtract.
    """
    area = (a + b) * Decimal("2") * h - openings_area
    return area if area > 0 else Decimal("0")


@dataclass(frozen=True)
class AccessoryRule:
    """One 'consumables' rule (master prompt v1 section 4): e.g. 1 m2 of tile needs X kg of
    adhesive. Sourced from the price repository, never hardcoded in the LLM prompt.
    """

    accessory_work_type: str
    quantity_per_base_unit: Decimal
    unit: str


class PriceRepositoryProtocol(Protocol):
    """The seam between `calculator.py` and the Ground Truth DB. Implemented for real by
    `price_repository.py` (backed by Postgres, a later step); unit tests use a simple
    in-memory fake so `EstimateCalculator` stays testable without a database.
    """

    def get_labor_rate(self, work_type: str) -> Decimal: ...

    def get_work_duration_days(self, work_type: str, quantity: Decimal) -> int:
        """Active labor duration (days) for `quantity` units of `work_type`, at this
        contractor's typical pace. Does NOT include curing/drying time."""
        ...

    def get_complexity_factor(self, work_type: str, work_item: WorkItem, is_old_building: bool) -> Decimal:
        """e.g. large-format tile (>120x60) -> 1.3; demolition in an old building -> 1.2."""
        ...

    def get_waste_factor(self, work_type: str, layout_pattern: str | None) -> Decimal:
        """e.g. straight tile +0.10, diagonal +0.15, gypsum board +0.10-0.12, paint/plaster
        +0.05-0.08, pipes/cable +0.10."""
        ...

    def get_material_unit_price(self, work_type: str, material: str | None, tier: MaterialTier) -> Decimal: ...

    def get_accessory_rules(self, work_type: str) -> list[AccessoryRule]: ...

    def get_logistics_surcharge_pct(self, floor_number: int | None, has_elevator: bool | None) -> Decimal:
        """0% for 1-2 floors with an elevator, up to 15% for a 5th floor with no elevator."""
        ...

    def get_seasonal_demand_multiplier(self, month: int) -> Decimal:
        """e.g. Decimal('1.10') in high season. 1.0 means no surcharge."""
        ...

    def get_fixed_overheads(self) -> dict[str, Decimal]:
        """e.g. {'site_visit': ..., 'waste_removal': ..., 'delivery': ...}."""
        ...

    def get_tax_rate(self) -> Decimal:
        """VAT rate, e.g. Decimal('0.23') for Poland."""
        ...

    def get_risk_baseline(self, precision_level: PrecisionLevelEnum) -> Decimal:
        """Baseline risk_coefficient for a precision level (LOW ~0.20-0.25, MID ~0.12-0.15,
        HIGH ~0.08-0.10), before the +0.05 old-building/hidden-conditions addons."""
        ...

    def get_contractor_profile(self) -> ContractorProfile: ...


@dataclass
class _ComputedItem:
    """Internal per-line-item pricing result, before tier-specific material cost is applied."""

    work_item: WorkItem
    labor_cost: Decimal
    base_material_qty: Decimal
    waste_factor: Decimal
    applied_factors: list[AppliedFactor]


# Human-readable (Polish) labels for `ExtractedRenovationData.missing_fields` entries, used
# by `_build_disclaimer` below. `missing_fields` entries look like internal field paths (e.g.
# "work_items[tiling_wall].unit") - never show those raw paths to the client, always translate
# the trailing field name via this table (falls back to the raw field name if not mapped).
_MISSING_FIELD_LABELS: dict[str, str] = {
    "quantity": "ilo\u015b\u0107/powierzchnia prac",
    "unit": "jednostka miary (m2, szt., mb)",
    "material": "rodzaj materia\u0142u",
    "tile_size_cm": "format p\u0142ytek",
    "layout_pattern": "spos\u00f3b uk\u0142adania p\u0142ytek",
    "substrate_condition": "stan pod\u0142o\u017ca",
    "is_old_building": "wiek budynku",
}


class EstimateCalculator:
    """Pure calculator: `ExtractedRenovationData` + `PriceRepositoryProtocol` -> `EstimateReport`.

    No side effects, no hidden state beyond the constructor arguments - safe to construct and
    call `calculate()` repeatedly, and trivial to unit test with a fake repository.
    """

    def __init__(self, data: ExtractedRenovationData, prices: PriceRepositoryProtocol) -> None:
        self._data = data
        self._prices = prices

    def calculate(self) -> EstimateReport:
        if self._data.precision_level == PrecisionLevelEnum.EXPERT_REQUIRED:
            return self._build_expert_required_report()

        computed_items = [self._compute_item(item) for item in self._data.work_items]
        computed_items += self._compute_accessory_items(computed_items)

        labor_total = sum((ci.labor_cost for ci in computed_items), Decimal("0"))
        project_factors, labor_total_with_surcharges = self._build_project_level_factors(labor_total)

        fixed_overheads = self._prices.get_fixed_overheads()
        overheads_total = sum(fixed_overheads.values(), Decimal("0"))

        tiers = _HIGH_PRECISION_TIERS if self._data.precision_level == PrecisionLevelEnum.HIGH else (None,)
        tax_rate = self._prices.get_tax_rate()
        risk_coefficient = self._compute_risk_coefficient()

        cost_breakdowns = [
            self._build_cost_breakdown(
                tier=tier,
                computed_items=computed_items,
                labor_total=labor_total_with_surcharges,
                overheads_total=overheads_total,
                risk_coefficient=risk_coefficient,
                tax_rate=tax_rate,
            )
            for tier in tiers
        ]

        line_items = self._build_line_items(computed_items, display_tier=MaterialTier.STANDARD)
        phase_schedule, estimated_duration_days = self._build_phase_schedule()

        return EstimateReport(
            precision_level=self._data.precision_level,
            requires_expert_handoff=False,
            line_items=line_items,
            cost_breakdowns=cost_breakdowns,
            project_level_factors=project_factors,
            fixed_overheads=fixed_overheads,
            phase_schedule=phase_schedule,
            estimated_duration_days=estimated_duration_days,
            exclusions=self._build_exclusions(),
            design_service_cost=self._price_design_service(),
            contractor_profile=self._prices.get_contractor_profile(),
            disclaimer=self._build_disclaimer(),
            clarifying_questions=self._data.clarifying_questions,
        )

    # ------------------------------------------------------------------ #
    # Line-item pricing
    # ------------------------------------------------------------------ #

    def _compute_item(self, item: WorkItem) -> _ComputedItem:
        quantity = Decimal(str(item.quantity)) if item.quantity is not None else Decimal("0")
        rate = self._prices.get_labor_rate(item.work_type)
        complexity_factor = self._prices.get_complexity_factor(
            item.work_type, item, bool(self._data.is_old_building)
        )
        waste_factor = self._prices.get_waste_factor(item.work_type, item.layout_pattern)

        base_labor = quantity * rate
        labor_cost = base_labor * complexity_factor

        applied_factors: list[AppliedFactor] = []
        if complexity_factor != Decimal("1"):
            applied_factors.append(
                AppliedFactor(
                    name=f"{item.work_type} complexity factor",
                    factor_type="complexity",
                    multiplier=complexity_factor - Decimal("1"),
                    amount=labor_cost - base_labor,
                )
            )

        return _ComputedItem(
            work_item=item,
            labor_cost=labor_cost,
            base_material_qty=quantity,
            waste_factor=waste_factor,
            applied_factors=applied_factors,
        )

    def _compute_accessory_items(self, computed_items: list[_ComputedItem]) -> list[_ComputedItem]:
        accessory_items: list[_ComputedItem] = []
        # Snapshot the parent list so accessories derived from one item don't themselves
        # spawn further accessories.
        for parent in list(computed_items):
            rules = self._prices.get_accessory_rules(parent.work_item.work_type)
            for rule in rules:
                accessory_qty = parent.base_material_qty * rule.quantity_per_base_unit
                synthetic = WorkItem(
                    work_type=rule.accessory_work_type,
                    room=parent.work_item.room,
                    quantity=float(accessory_qty),
                    unit=rule.unit if rule.unit in ("m2", "m", "pcs", "kg", "set") else None,
                    phase=parent.work_item.phase,
                    depends_on=parent.work_item.depends_on,
                    curing_days=0,
                    notes=f"Auto-derived accessory for '{parent.work_item.work_type}'.",
                )
                accessory_items.append(
                    _ComputedItem(
                        work_item=synthetic,
                        labor_cost=Decimal("0"),
                        base_material_qty=accessory_qty,
                        waste_factor=Decimal("0"),
                        applied_factors=[],
                    )
                )
        return accessory_items

    def _material_cost(self, ci: _ComputedItem, tier: MaterialTier) -> Decimal:
        unit_price = self._prices.get_material_unit_price(ci.work_item.work_type, ci.work_item.material, tier)
        return ci.base_material_qty * (Decimal("1") + ci.waste_factor) * unit_price

    def _build_line_items(
        self, computed_items: list[_ComputedItem], display_tier: MaterialTier
    ) -> list[EstimateLineItem]:
        return [
            EstimateLineItem(
                work_item=ci.work_item,
                quantity_with_waste=money(ci.base_material_qty * (Decimal("1") + ci.waste_factor)),
                labor_cost=money(ci.labor_cost),
                material_cost=money(self._material_cost(ci, display_tier)),
                applied_factors=ci.applied_factors,
            )
            for ci in computed_items
        ]

    # ------------------------------------------------------------------ #
    # Project-level surcharges (v2 section 5)
    # ------------------------------------------------------------------ #

    def _build_project_level_factors(self, labor_total: Decimal) -> tuple[list[AppliedFactor], Decimal]:
        factors: list[AppliedFactor] = []
        total = labor_total

        logistics_pct = self._prices.get_logistics_surcharge_pct(
            self._data.floor_number, self._data.has_elevator
        )
        if logistics_pct != Decimal("0"):
            amount = labor_total * logistics_pct
            floor_desc = f"floor {self._data.floor_number}" if self._data.floor_number is not None else "floor"
            elevator_desc = "no elevator" if self._data.has_elevator is False else "with elevator"
            factors.append(
                AppliedFactor(
                    name=f"{floor_desc}, {elevator_desc}",
                    factor_type="logistics",
                    multiplier=logistics_pct,
                    amount=amount,
                )
            )
            total += amount

        month = self._data.estimate_month
        if month is not None:
            seasonal_multiplier = self._prices.get_seasonal_demand_multiplier(month)
            if seasonal_multiplier != Decimal("1"):
                seasonal_pct = seasonal_multiplier - Decimal("1")
                amount = labor_total * seasonal_pct
                factors.append(
                    AppliedFactor(
                        name=f"Seasonal demand (month {month})",
                        factor_type="seasonal",
                        multiplier=seasonal_pct,
                        amount=amount,
                    )
                )
                total += amount

        return factors, total

    # ------------------------------------------------------------------ #
    # Risk buffer (v2 section 2)
    # ------------------------------------------------------------------ #

    def _compute_risk_coefficient(self) -> Decimal:
        coefficient = self._prices.get_risk_baseline(self._data.precision_level)
        if self._data.is_old_building:
            coefficient += OLD_BUILDING_RISK_ADDON
        if self._data.hidden_conditions_unknown:
            coefficient += HIDDEN_CONDITIONS_RISK_ADDON
        return coefficient

    def _build_cost_breakdown(
        self,
        tier: MaterialTier | None,
        computed_items: list[_ComputedItem],
        labor_total: Decimal,
        overheads_total: Decimal,
        risk_coefficient: Decimal,
        tax_rate: Decimal,
    ) -> CostBreakdown:
        material_total = sum(
            (self._material_cost(ci, tier or MaterialTier.STANDARD) for ci in computed_items),
            Decimal("0"),
        )
        subtotal = labor_total + material_total + overheads_total
        risk_buffer_amount = subtotal * risk_coefficient
        taxable = subtotal + risk_buffer_amount
        tax_amount = taxable * tax_rate
        total = taxable + tax_amount

        return CostBreakdown(
            tier=tier,
            labor_cost=money(labor_total),
            material_cost=money(material_total),
            subtotal=money(subtotal),
            risk_coefficient=risk_coefficient,
            risk_buffer_amount=money(risk_buffer_amount),
            tax_rate=tax_rate,
            tax_amount=money(tax_amount),
            total=money(total),
            currency=self._data.currency,
        )

    # ------------------------------------------------------------------ #
    # Phase graph & duration (v2 section 1)
    # ------------------------------------------------------------------ #

    def _build_phase_schedule(self) -> tuple[list[PhaseScheduleItem], int | None]:
        items_by_phase: dict[WorkPhase, list[WorkItem]] = {}
        for item in self._data.work_items:
            items_by_phase.setdefault(item.phase, []).append(item)

        if not items_by_phase:
            return [], None

        depends_on: dict[WorkPhase, set[WorkPhase]] = {
            phase: set(dep for i in items for dep in i.depends_on if dep in items_by_phase)
            for phase, items in items_by_phase.items()
        }
        work_duration: dict[WorkPhase, int] = {
            phase: max(
                1,
                sum(
                    self._prices.get_work_duration_days(
                        i.work_type, Decimal(str(i.quantity)) if i.quantity is not None else Decimal("0")
                    )
                    for i in items
                ),
            )
            for phase, items in items_by_phase.items()
        }
        curing_days: dict[WorkPhase, int] = {
            phase: max((i.curing_days for i in items), default=0) for phase, items in items_by_phase.items()
        }

        ends_on_day: dict[WorkPhase, int] = {}
        starts_after_day: dict[WorkPhase, int] = {}

        remaining = set(items_by_phase.keys())
        resolved: set[WorkPhase] = set()
        while remaining:
            ready = [p for p in remaining if depends_on[p] <= resolved]
            if not ready:
                raise ValueError(f"Cyclic WorkPhase dependency detected among: {remaining}")
            for phase in ready:
                start = max((ends_on_day[dep] for dep in depends_on[phase]), default=0)
                starts_after_day[phase] = start
                ends_on_day[phase] = start + work_duration[phase] + curing_days[phase]
                resolved.add(phase)
                remaining.discard(phase)

        schedule = [
            PhaseScheduleItem(
                phase=phase,
                starts_after_day=starts_after_day[phase],
                work_duration_days=work_duration[phase],
                curing_days=curing_days[phase],
                ends_on_day=ends_on_day[phase],
            )
            for phase in items_by_phase
        ]
        schedule.sort(key=lambda s: s.starts_after_day)
        return schedule, max(ends_on_day.values())

    # ------------------------------------------------------------------ #
    # Exclusions (v2 section 2 + 7)
    # ------------------------------------------------------------------ #

    def _build_exclusions(self) -> list[str]:
        exclusions = [
            "Ukryte usterki odkryte po demonta\u017cu (np. stan instalacji lub konstrukcji) "
            "nie s\u0105 uwzgl\u0119dnione w tej wycenie.",
            "Pozwolenia i zgody urz\u0119dowe, je\u015bli wymagane, nie s\u0105 uj\u0119te w tym kosztorysie.",
        ]
        if self._data.is_old_building:
            exclusions.append(
                "Budynek sprzed 1970 r. - stan konstrukcji no\u015bnej mo\u017ce wymaga\u0107 "
                "dodatkowej ekspertyzy, nieuwzgl\u0119dnionej w tym kosztorysie."
            )
            exclusions.append(
                "Demonta\u017c w starym budynku mo\u017ce ods\u0142oni\u0107 materia\u0142y "
                "zawieraj\u0105ce azbest (stary klej do p\u0142ytek, izolacje rur) - wymagana "
                "odr\u0119bna ekspertyza i utylizacja."
            )
        if self._data.hidden_conditions_unknown:
            exclusions.append(
                "Stan pod\u0142o\u017ca/\u015bcian nie by\u0142 weryfikowany przed wycen\u0105 - "
                "ostateczny koszt przygotowania pod\u0142o\u017ca mo\u017ce si\u0119 r\u00f3\u017cni\u0107."
            )
        return exclusions

    def _build_disclaimer(self) -> str | None:
        if self._data.precision_level != PrecisionLevelEnum.MID:
            return None
        if self._data.missing_fields:
            labels: list[str] = []
            for field_path in self._data.missing_fields:
                field_name = field_path.rsplit(".", 1)[-1]
                label = _MISSING_FIELD_LABELS.get(field_name, field_name)
                if label not in labels:
                    labels.append(label)
            return (
                "Wycena wg standardowych za\u0142o\u017ce\u0144. Doprecyzuj: "
                f"{', '.join(labels)}, aby otrzyma\u0107 dok\u0142adny kosztorys."
            )
        return "Wycena wg standardowych za\u0142o\u017ce\u0144 rynkowych."

    # ------------------------------------------------------------------ #
    # Design service (v2 section 4) - priced separately, never merged in
    # ------------------------------------------------------------------ #

    def _price_design_service(self) -> Decimal | None:
        request = self._data.design_service
        if request is None or not request.needed:
            return None

        if request.service_type == DesignServiceType.PERCENT_OF_BUDGET:
            if request.fee_percent is None:
                raise ValueError("design_service.fee_percent is required for PERCENT_OF_BUDGET.")
            construction_subtotal = sum(
                (self._compute_item(i).labor_cost for i in self._data.work_items), Decimal("0")
            ) + sum(
                (self._material_cost(self._compute_item(i), MaterialTier.STANDARD) for i in self._data.work_items),
                Decimal("0"),
            )
            return money(construction_subtotal * request.fee_percent)

        if request.service_type == DesignServiceType.FIXED_CONCEPT_FEE:
            if request.fixed_fee is None:
                raise ValueError("design_service.fixed_fee is required for FIXED_CONCEPT_FEE.")
            return money(request.fixed_fee)

        if request.service_type == DesignServiceType.PER_SQM_DOCUMENTATION:
            if request.price_per_sqm is None:
                raise ValueError("design_service.price_per_sqm is required for PER_SQM_DOCUMENTATION.")
            if not self._data.total_area_m2:
                raise ValueError("total_area_m2 is required to price PER_SQM_DOCUMENTATION.")
            return money(request.price_per_sqm * Decimal(str(self._data.total_area_m2)))

        raise ValueError(f"design_service.needed is True but service_type is missing/unknown: {request.service_type}")

    # ------------------------------------------------------------------ #
    # Expert-required short-circuit (v2 section 3)
    # ------------------------------------------------------------------ #

    def _build_expert_required_report(self) -> EstimateReport:
        return EstimateReport(
            precision_level=PrecisionLevelEnum.EXPERT_REQUIRED,
            requires_expert_handoff=True,
            expert_handoff_message=(
                "To obiekt wymagaj\u0105cy uzgodnienia z konserwatorem zabytk\u00f3w i udzia\u0142u "
                "eksperta-renowatora. Automatyczna wycena nie jest dost\u0119pna - przekazuj\u0119 "
                "zg\u0142oszenie do mened\u017cera."
            ),
            line_items=[],
            cost_breakdowns=[],
            project_level_factors=[],
            fixed_overheads={},
            phase_schedule=[],
            estimated_duration_days=None,
            exclusions=[],
            design_service_cost=None,
            contractor_profile=None,
            disclaimer=None,
            clarifying_questions=[],
        )
