"""Regression tests for `pdf_generator.py`.

`generate_estimate_html` is pure (no native dependencies) and tested extensively here using
real `EstimateReport`/`ExtractedRenovationData` objects produced by `EstimateCalculator` (the
same `FakePriceRepository` + `_high_precision_data` fixtures as `tests/test_calculator.py`,
reused rather than duplicated).

`generate_estimate_pdf` additionally requires WeasyPrint's native Pango/GLib/cairo libraries;
that one test is skipped gracefully (not failed) if they aren't importable in this environment,
mirroring the DB-integration skip pattern in `tests/conftest.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from test_calculator import FakePriceRepository, _high_precision_data

from calculator import EstimateCalculator
from pdf_generator import WEASYPRINT_IMPORT_ERROR, generate_estimate_html, generate_estimate_pdf, weasyprint
from schema import (
    DesignServiceRequest,
    DesignServiceType,
    ExtractedRenovationData,
    PrecisionLevelEnum,
)

FIXED_GENERATED_AT = datetime(2026, 8, 12, tzinfo=timezone.utc)


@pytest.fixture
def prices() -> FakePriceRepository:
    return FakePriceRepository()


def test_html_renders_expert_required_handoff_without_pricing_sections(prices: FakePriceRepository) -> None:
    data = ExtractedRenovationData(
        precision_level=PrecisionLevelEnum.EXPERT_REQUIRED,
        is_heritage_site=True,
        heritage_keywords_matched=["zabytek"],
    )
    report = EstimateCalculator(data, prices).calculate()

    html = generate_estimate_html(report, data, generated_at=FIXED_GENERATED_AT)

    assert "konserwatorem" in html
    assert "Zakres prac" not in html  # no line-items table for a handoff-only document
    assert "Podsumowanie kosztów" not in html


def test_html_renders_high_precision_three_tier_table(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(), prices).calculate()
    data = _high_precision_data()

    html = generate_estimate_html(report, data, generated_at=FIXED_GENERATED_AT)

    assert "Ekonomiczny" in html
    assert "Standardowy" in html
    assert "Premium" in html
    assert "RAZEM" in html
    # money formatting: Polish thousands separator + comma decimal
    total = report.cost_breakdowns[0].total
    assert f"{total:,.2f}".replace(",", "\u00a0").replace(".", ",") in html


def test_html_renders_exclusions_section(prices: FakePriceRepository) -> None:
    data = _high_precision_data(is_old_building=True)
    report = EstimateCalculator(data, prices).calculate()

    html = generate_estimate_html(report, data, generated_at=FIXED_GENERATED_AT)

    assert "Co NIE wchodzi w zakres kosztorysu" in html
    assert any(excl in html for excl in report.exclusions)


def test_html_renders_phase_schedule_and_duration(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(), prices).calculate()
    data = _high_precision_data()

    html = generate_estimate_html(report, data, generated_at=FIXED_GENERATED_AT)

    assert "Harmonogram robót" in html
    assert f"{report.estimated_duration_days} dni" in html


def test_html_renders_design_service_separately_from_construction_total(prices: FakePriceRepository) -> None:
    data = _high_precision_data(
        design_service=DesignServiceRequest(
            needed=True, service_type=DesignServiceType.PERCENT_OF_BUDGET, fee_percent=Decimal("0.10")
        )
    )
    report = EstimateCalculator(data, prices).calculate()

    html = generate_estimate_html(report, data, generated_at=FIXED_GENERATED_AT)

    assert "Usługa projektowa (osobno od budowy)" in html
    formatted = f"{report.design_service_cost:,.2f}".replace(",", "\u00a0").replace(".", ",")
    assert formatted in html


def test_html_omits_design_service_section_when_not_requested(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(), prices).calculate()
    data = _high_precision_data()

    html = generate_estimate_html(report, data, generated_at=FIXED_GENERATED_AT)

    assert "Usługa projektowa" not in html


def test_html_renders_contractor_payment_schedule_and_warranty(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(), prices).calculate()
    data = _high_precision_data()

    html = generate_estimate_html(report, data, generated_at=FIXED_GENERATED_AT)

    assert "Warunki umowy" in html
    assert "Test Renowacje" in html
    assert "tiling" in html


def test_html_renders_mid_precision_single_summary_table_and_disclaimer(prices: FakePriceRepository) -> None:
    data = ExtractedRenovationData(precision_level=PrecisionLevelEnum.MID)
    report = EstimateCalculator(data, prices).calculate()
    assert report.disclaimer is not None  # calculator.py mandates a disclaimer at MID precision

    html = generate_estimate_html(report, data, generated_at=FIXED_GENERATED_AT)

    assert report.disclaimer in html
    assert html.count("RAZEM") == 1  # single cost_breakdown, not the 3-tier table


def test_html_precision_badge_shown(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(), prices).calculate()
    data = _high_precision_data()

    html = generate_estimate_html(report, data, generated_at=FIXED_GENERATED_AT)

    assert "Szczegółowy (HIGH)" in html


@pytest.mark.skipif(
    weasyprint is None,
    reason=f"WeasyPrint native libraries not importable in this environment: {WEASYPRINT_IMPORT_ERROR}",
)
def test_generate_estimate_pdf_produces_valid_pdf_bytes(prices: FakePriceRepository) -> None:
    report = EstimateCalculator(_high_precision_data(), prices).calculate()
    data = _high_precision_data()

    pdf_bytes = generate_estimate_pdf(report, data, generated_at=FIXED_GENERATED_AT)

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 500
