"""Domain data models for the Smart Estimate Bot (Kosztorys-Engine).

Architectural rule (see master prompt, section 1): the LLM parser (`llm_parser.py`) is
allowed to populate these models with facts extracted from free text/voice/photo, but it
must NEVER compute money amounts. All arithmetic lives in `calculator.py`. `schema.py`
itself contains no business logic - only typed, validated data containers.

Money fields use `Decimal` (never `float`) to avoid binary floating-point rounding errors
in financial calculations.

v2 domain extension (master prompt v2) adds: technological phases + curing/drying
durations (an estimate without a timeline is incomplete), an explicit risk buffer for
hidden/undiscovered conditions, a hard "expert required" precision tier for heritage
sites (the system must refuse to price these, not guess), a separately-priced design
service, logistics/seasonal surcharges shown line-by-line, three parallel material
tiers (economy/standard/premium) at HIGH precision, and contractual sections
(payment schedule, warranty terms, exclusions) for the final PDF.

--------------------------------------------------------------------------------------
EXAMPLES: how `ExtractedRenovationData` looks at each precision tier
--------------------------------------------------------------------------------------

1) LOW PRECISION - vague request, only a room and rough area are known.
   User said: "ремонт ванной 5 кв.м" ("bathroom renovation, 5 sq.m").

    ExtractedRenovationData(
        raw_text="ремонт ванной 5 кв.м",
        country="PL",
        currency="PLN",
        rooms=["bathroom"],
        total_area_m2=5.0,
        work_items=[
            WorkItem(work_type="bathroom_renovation_generic", room="bathroom",
                      quantity=5.0, unit="m2", phase=WorkPhase.FINISH),
        ],
        precision_level=PrecisionLevelEnum.LOW,
        missing_fields=["work_items.material", "work_items.layout_pattern",
                         "substrate_condition"],
        clarifying_questions=[
            "Jaki rodzaj płytek planujesz użyć (i w jakim formacie)?",
            "Czy ściany/podłoga wymagają rozbiórki starych płytek?",
            "Czy potrzebna jest wymiana instalacji hydraulicznej/elektrycznej?",
        ],
    )

2) MID PRECISION - main work types are named, but materials/substrate state are missing.
   User said: "нужно положить плитку на пол и стены в ванной, старую плитку снять".

    ExtractedRenovationData(
        raw_text="нужно положить плитку на пол и стены в ванной, старую плитку снять",
        country="PL",
        currency="PLN",
        rooms=["bathroom"],
        total_area_m2=5.0,
        work_items=[
            WorkItem(work_type="demolition_tiling", room="bathroom", quantity=5.0, unit="m2",
                      phase=WorkPhase.DEMOLITION),
            WorkItem(work_type="tiling_floor", room="bathroom", quantity=5.0, unit="m2",
                      layout_pattern="straight", phase=WorkPhase.FINISH,
                      depends_on=[WorkPhase.DEMOLITION]),
            WorkItem(work_type="tiling_wall", room="bathroom", quantity=12.0, unit="m2",
                      layout_pattern="straight", phase=WorkPhase.FINISH,
                      depends_on=[WorkPhase.DEMOLITION]),
        ],
        precision_level=PrecisionLevelEnum.MID,
        missing_fields=["work_items.material", "work_items.tile_size_cm",
                         "substrate_condition"],
        clarifying_questions=[
            "Jaki format płytek (np. 30x30, 60x60, 120x60)?",
            "W jakim stanie jest obecne podłoże po demontażu?",
        ],
    )

3) HIGH PRECISION - exact parameters are given, ready for a full costed estimate,
   including a screed with a 28-day curing time before tiling can start.
   User said: "ванная 5 кв.м, стяжка пола, пол+стены (12 кв.м), плитка 120x60 по
              диагонали, старая плитка под снос, дом старой застройки, 3 точки
              электрики, 5 этаж без лифта".

    ExtractedRenovationData(
        raw_text="...",
        country="PL",
        currency="PLN",
        rooms=["bathroom"],
        total_area_m2=5.0,
        is_old_building=True,
        work_items=[
            WorkItem(work_type="demolition_tiling", room="bathroom", quantity=17.0,
                      unit="m2", substrate_condition="poor", phase=WorkPhase.DEMOLITION),
            WorkItem(work_type="screed", room="bathroom", quantity=5.0, unit="m2",
                      phase=WorkPhase.SCREED, depends_on=[WorkPhase.DEMOLITION],
                      curing_days=28),
            WorkItem(work_type="tiling_floor", room="bathroom", quantity=5.0, unit="m2",
                      material="ceramic tile", tile_size_cm="120x60",
                      layout_pattern="diagonal", phase=WorkPhase.FINISH,
                      depends_on=[WorkPhase.SCREED]),
            WorkItem(work_type="tiling_wall", room="bathroom", quantity=12.0, unit="m2",
                      material="ceramic tile", tile_size_cm="120x60",
                      layout_pattern="diagonal", phase=WorkPhase.FINISH,
                      depends_on=[WorkPhase.DEMOLITION]),
            WorkItem(work_type="electrical_point", room="bathroom", quantity=3.0,
                      unit="pcs", phase=WorkPhase.ROUGH_MEP,
                      depends_on=[WorkPhase.DEMOLITION]),
        ],
        precision_level=PrecisionLevelEnum.HIGH,
        missing_fields=[],
        clarifying_questions=[],
    )
    # calculator.py then produces THREE CostBreakdown entries (economy/standard/premium),
    # a phase_schedule with estimated_duration_days accounting for the 28-day screed cure,
    # and a logistics AppliedFactor line ("5th floor, no elevator: +15%").

4) EXPERT_REQUIRED - heritage/protected-monument keywords detected. The calculator must
   NOT produce a price at all; the bot hands off to a human instead.
   User said: "ремонт в историческом здании, охраняется как памятник архитектуры,
              нужно сохранить лепнину на потолке".

    ExtractedRenovationData(
        raw_text="ремонт в историческом здании, охраняется как памятник архитектуры, "
                  "нужно сохранить лепнину на потолке",
        country="PL",
        currency="PLN",
        rooms=["living_room"],
        is_heritage_site=True,
        heritage_keywords_matched=["памятник архитектуры", "лепнина"],
        precision_level=PrecisionLevelEnum.EXPERT_REQUIRED,
        work_items=[],
        missing_fields=[],
        clarifying_questions=[],
    )
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class PrecisionLevelEnum(str, Enum):
    """How much detail the extracted data contains, which drives the estimate's accuracy.

    Assigned deterministically by `llm_parser.py` based on how many required fields were
    successfully extracted from the user's input - never guessed "by feel" inside the LLM
    prompt itself. See master prompt section 3 for the exact criteria per level.
    """

    LOW = "low"
    """Vague input. Budget range only (~30-40% error margin), from an averaged m2 price."""

    MID = "mid"
    """Main work types named, but materials/substrate state are missing (~15-20% error)."""

    HIGH = "high"
    """All parameters known: full itemized estimate with taxes (±5% error)."""

    EXPERT_REQUIRED = "expert_required"
    """Heritage/protected-monument markers detected (master prompt v2 section 3). The
    calculator MUST NOT compute a price at all - it is professionally and legally risky to
    produce a pseudo-precise number here. The bot instead hands off to a human expert."""


class WorkPhase(str, Enum):
    """Technological sequence stage a `WorkItem` belongs to (master prompt v2 section 1).

    Used by `calculator.py` to build a dependency graph and compute
    `estimated_duration_days`, accounting for curing/drying time between phases (e.g. a
    5cm screed needs ~28 days before tiling). An estimate without a timeline is incomplete.
    """

    DEMOLITION = "demolition"
    ROUGH_MEP = "rough_electrical_plumbing"
    SCREED = "screed"
    PLASTER = "plaster"
    FINISH = "finish"
    ENGINEERING = "engineering_systems"
    FACADE_ROOF = "facade_roof"


class MaterialTier(str, Enum):
    """Material quality tier (master prompt v2 section 6). At HIGH precision, the report
    presents three parallel `CostBreakdown` columns instead of a single number, letting
    the client choose rather than having one budget imposed on them.
    """

    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"


class DesignServiceType(str, Enum):
    """Pricing model for a separate interior-design service (master prompt v2 section 4).
    Always priced and reported apart from the construction budget - never merged into one
    sum without a clear label.
    """

    PERCENT_OF_BUDGET = "percent_of_budget"
    """Typically 8-15% of the construction budget."""

    FIXED_CONCEPT_FEE = "fixed_concept_fee"
    PER_SQM_DOCUMENTATION = "per_sqm_documentation"


class WorkItem(BaseModel):
    """A single work/material line item extracted from free-form user input.

    Captures WHAT the user wants (work type, room, quantity, and any complexity/waste
    hints) - never a price. Unit rates, material prices, and factor coefficients are
    looked up later from `PriceRepository` (the Ground Truth DB), never guessed by the LLM.
    """

    work_type: str = Field(
        ...,
        description=(
            "Normalized work type identifier, e.g. 'tiling_floor', 'demolition_tiling', "
            "'electrical_point'. Must match a known key in the price catalog."
        ),
    )
    room: str | None = Field(
        default=None,
        description="Room/area this work applies to, e.g. 'bathroom', 'kitchen'.",
    )
    quantity: float | None = Field(
        default=None,
        ge=0,
        description="Extracted quantity in the unit given by `unit` (area, length, or count).",
    )
    unit: Literal["m2", "m", "pcs", "kg", "set"] | None = Field(
        default=None,
        description="Unit of measurement for `quantity`.",
    )
    material: str | None = Field(
        default=None,
        description="Material name/type mentioned by the user, e.g. 'ceramic tile', 'gypsum board'.",
    )
    tile_size_cm: str | None = Field(
        default=None,
        description=(
            "Tile format as extracted from text, e.g. '120x60'. Used by the calculator "
            "to look up the large-format complexity factor (>120x60 -> x1.3 labor)."
        ),
    )
    layout_pattern: Literal["straight", "diagonal"] | None = Field(
        default=None,
        description="Tiling pattern, affects the waste factor (straight +10%, diagonal +15%).",
    )
    substrate_condition: Literal["good", "poor", "unknown"] | None = Field(
        default=None,
        description=(
            "State of the wall/floor substrate, e.g. for demolition work in an old "
            "building (-> x1.2 labor complexity factor)."
        ),
    )
    notes: str | None = Field(
        default=None,
        description="Any free-form detail extracted for this item that doesn't fit other fields.",
    )
    phase: WorkPhase = Field(
        ...,
        description=(
            "Technological sequence stage this item belongs to (master prompt v2 section 1). "
            "Required so the calculator can build the phase dependency graph."
        ),
    )
    depends_on: list[WorkPhase] = Field(
        default_factory=list,
        description="Hard technological dependencies - phases that must complete (incl. curing) first.",
    )
    curing_days: int = Field(
        default=0,
        ge=0,
        description=(
            "Drying/curing time in days before a dependent phase may start, "
            "e.g. 28 for a 5cm screed, or per-layer drying time for plaster."
        ),
    )


class AppliedFactor(BaseModel):
    """A single named surcharge/multiplier applied to a line item (master prompt v2
    section 5): logistics (floor/elevator), seasonal (wet-process/demand), or the v1
    complexity/waste factors. Shown itemized in the report so the client sees WHY a cost
    changed, e.g. '5th floor, no elevator: +15%' - never folded silently into a total.
    """

    name: str = Field(..., description="Human-readable label, e.g. '5th floor, no elevator'.")
    factor_type: Literal["complexity", "waste", "logistics", "seasonal"]
    multiplier: Decimal = Field(..., description="Rate applied, e.g. Decimal('0.15') for +15%.")
    amount: Decimal = Field(..., description="Resulting money impact of this factor on the line item.")


class ExtractedRenovationData(BaseModel):
    """Structured output of `llm_parser.py`: the single source of truth handed to
    `calculator.py`. Fully populated from user text/voice/photo - contains no computed
    money values.
    """

    raw_text: str | None = Field(
        default=None,
        description="Original user message (for audit/debugging/re-parsing).",
    )
    country: Literal["PL"] = Field(
        default="PL",
        description="Pilot market is Poland; other CEE countries added without core rewrites.",
    )
    currency: Literal["PLN", "EUR"] = Field(default="PLN")
    city: str | None = Field(default=None)
    rooms: list[str] = Field(default_factory=list)
    total_area_m2: float | None = Field(default=None, ge=0)
    is_old_building: bool | None = Field(
        default=None,
        description="Whether the building is old construction (affects demolition complexity).",
    )
    work_items: list[WorkItem] = Field(default_factory=list)
    photo_notes: list[str] = Field(
        default_factory=list,
        description="Facts extracted from photos via Vision (e.g. Gemini), merged as plain text notes.",
    )
    is_heritage_site: bool = Field(
        default=False,
        description=(
            "True if llm_parser detected cultural-heritage/protected-monument keywords "
            "(e.g. 'zabytek', 'konserwator zabytk\u00f3w', 'lepnina/fresco'). Forces "
            "precision_level=EXPERT_REQUIRED and blocks automatic pricing (master prompt v2 §3)."
        ),
    )
    heritage_keywords_matched: list[str] = Field(
        default_factory=list,
        description="Audit trail of which keywords triggered is_heritage_site, for review/debugging.",
    )
    design_service: DesignServiceRequest | None = Field(
        default=None,
        description=(
            "Populated only after llm_parser explicitly asked whether a design project is "
            "needed or already exists (master prompt v2 §4). Priced completely separately "
            "from the construction budget - never merged into one sum."
        ),
    )
    hidden_conditions_unknown: bool = Field(
        default=False,
        description=(
            "True if the walls/floor substrate were never opened up/inspected before this "
            "estimate (master prompt v2 §2). Adds +0.05 to the risk_coefficient in calculator.py."
        ),
    )
    floor_number: int | None = Field(
        default=None,
        ge=0,
        description="Floor the work site is on. Used to look up the logistics surcharge (master prompt v2 §5).",
    )
    has_elevator: bool | None = Field(
        default=None,
        description="Whether the building has a working elevator. Used with floor_number for the logistics surcharge.",
    )
    estimate_month: int | None = Field(
        default=None,
        ge=1,
        le=12,
        description=(
            "Calendar month (1-12) the work is planned for, used to look up the seasonal "
            "demand/wet-process factor (master prompt v2 §5). Defaults to the current month if unset."
        ),
    )

    precision_level: PrecisionLevelEnum = Field(
        ...,
        description="Assigned by llm_parser.py based on completeness of extracted fields.",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Dotted field paths still missing to reach the next precision tier.",
    )
    clarifying_questions: list[str] = Field(
        default_factory=list,
        description="3-5 questions to ask the user to move from LOW/MID towards HIGH precision.",
    )


class DesignServiceRequest(BaseModel):
    """A separate interior-design service request (master prompt v2 section 4), priced and
    reported apart from the construction budget - never mixed into one sum without a clear
    label. Exactly one of `fee_percent` / `fixed_fee` / `price_per_sqm` is meaningful,
    depending on `service_type`.
    """

    needed: bool = Field(..., description="False if the client already has a design project.")
    service_type: DesignServiceType | None = Field(default=None)
    fee_percent: Decimal | None = Field(
        default=None,
        description="Used when service_type=PERCENT_OF_BUDGET, typically 0.08-0.15.",
    )
    fixed_fee: Decimal | None = Field(
        default=None,
        description="Used when service_type=FIXED_CONCEPT_FEE.",
    )
    price_per_sqm: Decimal | None = Field(
        default=None,
        description="Used when service_type=PER_SQM_DOCUMENTATION.",
    )


class PaymentMilestone(BaseModel):
    """One line of the contractor's payment schedule (master prompt v2 section 7). The
    percentage split is sourced from `ContractorProfile`, never hardcoded in a template.
    """

    label: str = Field(..., description="e.g. 'Advance', 'After screed & rough-in', 'Final payment'.")
    percent: Decimal = Field(..., description="Share of the total contract sum, e.g. Decimal('0.30').")
    trigger: str = Field(..., description="Human-readable condition that releases this payment.")


class WarrantyTerm(BaseModel):
    """Warranty period for one work category (master prompt v2 section 7) - tiling,
    plumbing, and electrical typically carry different statutory/contractual terms, so this
    is a table sourced from the DB, not a single number guessed by the LLM.
    """

    work_category: str = Field(..., description="e.g. 'tiling', 'plumbing', 'electrical'.")
    warranty_months: int = Field(..., ge=0)


class ContractorProfile(BaseModel):
    """Per-contractor configuration used to render the contractual sections of the PDF
    (master prompt v2 section 7). Sourced from the DB (see `db/models.py`), never
    hardcoded in `pdf_generator.py` templates or the LLM prompt.
    """

    company_name: str
    payment_schedule: list[PaymentMilestone] = Field(default_factory=list)
    warranty_terms: list[WarrantyTerm] = Field(default_factory=list)


class CostBreakdown(BaseModel):
    """Aggregate monetary result of `EstimateCalculator` for ONE material tier, per the
    v2 risk-buffer formula (master prompt v2 section 2), which supersedes the v1 generic
    "contingency margin":

        Subtotal       = Labor Cost + Material Cost
        Risk_Buffer    = Subtotal * risk_coefficient
        Total Estimate = (Subtotal + Risk_Buffer) * (1 + Tax Rate)

    `risk_coefficient` is assembled deterministically in `calculator.py` from:
    precision_level baseline (LOW 0.20-0.25, MID 0.12-0.15, HIGH 0.08-0.10) +
    building_age_flag (+0.05 if pre-1970) + hidden_conditions_unknown (+0.05 if the
    walls/floor were never opened up) - never guessed by the LLM.

    At HIGH precision, exactly three `CostBreakdown` instances are produced (one per
    `MaterialTier`), sharing the same `labor_cost` but differing in `material_cost`.
    """

    tier: MaterialTier | None = Field(
        default=None,
        description="None for LOW/MID (single-tier); set for each of the 3 HIGH-precision breakdowns.",
    )
    labor_cost: Decimal
    material_cost: Decimal
    subtotal: Decimal
    risk_coefficient: Decimal = Field(description="Assembled rate, e.g. Decimal('0.20') for 20%.")
    risk_buffer_amount: Decimal
    tax_rate: Decimal = Field(description="VAT rate applied, e.g. Decimal('0.23') for Polish VAT.")
    tax_amount: Decimal
    total: Decimal
    currency: Literal["PLN", "EUR"]


class EstimateLineItem(BaseModel):
    """Costed version of a `WorkItem`, after `EstimateCalculator` has applied rates,
    complexity factors, and waste factors sourced from `PriceRepository`.
    """

    work_item: WorkItem
    quantity_with_waste: Decimal | None = Field(
        default=None,
        description="Material quantity after applying the waste factor.",
    )
    labor_cost: Decimal
    material_cost: Decimal
    applied_factors: list[AppliedFactor] = Field(
        default_factory=list,
        description=(
            "Logistics/seasonal/complexity/waste surcharges applied to this item, itemized "
            "so the client sees WHY, e.g. '5th floor, no elevator: +15%' (master prompt v2 §5)."
        ),
    )


class PhaseScheduleItem(BaseModel):
    """One node of the phase dependency graph built by `calculator.py` (master prompt v2
    section 1), used to compute `EstimateReport.estimated_duration_days`.
    """

    phase: WorkPhase
    starts_after_day: int = Field(..., ge=0, description="Project day this phase can begin on.")
    work_duration_days: int = Field(..., ge=0, description="Active labor duration for this phase.")
    curing_days: int = Field(..., ge=0, description="Drying/curing time after work, before dependents may start.")
    ends_on_day: int = Field(..., ge=0, description="starts_after_day + work_duration_days + curing_days.")


class EstimateReport(BaseModel):
    """Final output handed to `pdf_generator.py` and sent back to the user.

    When `requires_expert_handoff` is True (EXPERT_REQUIRED precision), `cost_breakdowns`,
    `line_items`, and `phase_schedule` are all empty - the calculator produced NO price,
    only the handoff message (master prompt v2 section 3).
    """

    precision_level: PrecisionLevelEnum
    requires_expert_handoff: bool = Field(
        default=False,
        description="True when precision_level == EXPERT_REQUIRED; a human takes over instead of pricing.",
    )
    expert_handoff_message: str | None = Field(default=None)

    line_items: list[EstimateLineItem] = Field(default_factory=list)
    cost_breakdowns: list[CostBreakdown] = Field(
        default_factory=list,
        description="One entry for LOW/MID; exactly three (economy/standard/premium) for HIGH; empty for EXPERT_REQUIRED.",
    )
    project_level_factors: list[AppliedFactor] = Field(
        default_factory=list,
        description=(
            "Logistics/seasonal surcharges applied to the whole project's labor cost rather "
            "than a single line item (master prompt v2 §5), e.g. '5th floor, no elevator: +15%'."
        ),
    )
    fixed_overheads: dict[str, Decimal] = Field(
        default_factory=dict,
        description="Fixed project overheads (site visit, waste removal, delivery), shown as their own subtotal component.",
    )
    phase_schedule: list[PhaseScheduleItem] = Field(default_factory=list)
    estimated_duration_days: int | None = Field(
        default=None,
        description="Total project duration including curing/drying time, from the phase graph.",
    )
    exclusions: list[str] = Field(
        default_factory=list,
        description=(
            "Auto-generated 'What's NOT included' list (master prompt v2 §2), derived from "
            "the same flags that feed risk_coefficient - never hand-written per estimate."
        ),
    )
    design_service_cost: Decimal | None = Field(
        default=None,
        description="Cost of the separate design-project service, if requested. Never added into cost_breakdowns totals.",
    )
    contractor_profile: ContractorProfile | None = Field(
        default=None,
        description="Used by pdf_generator.py to render the payment schedule & warranty terms sections.",
    )

    disclaimer: str | None = Field(
        default=None,
        description=(
            "Mandatory for MID precision, e.g. 'Estimate based on standard averages. "
            "Clarify X and Y for a precise quote.'"
        ),
    )
    clarifying_questions: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
