"""HTML/PDF report renderer: `EstimateReport` + `ExtractedRenovationData` -> a *Kosztorys
Budowlany* PDF document (master prompt v1 section 5: "Jinja2 + weasyprint").

This module is a pure presentation layer - it renders exactly what `calculator.py` already
computed and makes NO pricing decisions of its own. It adds the mandatory sections required
by master prompt v2:

- section 2: "Co NIE wchodzi w zakres kosztorysu" (what's NOT included) - straight from
  `report.exclusions`, never hand-written per estimate.
- section 6: three parallel material-tier columns (economy/standard/premium) at HIGH
  precision, sharing one labor cost - never a single imposed number.
- section 7: the contract block (payment schedule + warranty terms), sourced from
  `report.contractor_profile` (itself DB-backed, see `db/models.py`) - never hardcoded here.

Split into `generate_estimate_html` (pure, no native dependencies, fully unit-testable) and
`generate_estimate_pdf` (needs WeasyPrint + its native Pango/GLib/cairo libraries) so the bulk
of the rendering logic can be tested everywhere without requiring those system libraries.

macOS + Apple Silicon note: WeasyPrint needs the native Pango/GLib/cairo libraries, which on
an ARM Mac live under the ARM Homebrew prefix (`/opt/homebrew`) rather than the Intel prefix
(`/usr/local`) that `dlopen` searches by default. We add both to `DYLD_FALLBACK_LIBRARY_PATH`
before importing WeasyPrint, guarded to `darwin` only - a no-op on Linux prod/CI, where the
Dockerfile instead `apt-get install`s the equivalent system packages.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

if sys.platform == "darwin":  # pragma: no cover - platform-specific, not exercised in CI
    _HOMEBREW_LIB_DIRS = "/opt/homebrew/lib:/usr/local/lib"
    _existing_path = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if _HOMEBREW_LIB_DIRS not in _existing_path:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
            f"{_HOMEBREW_LIB_DIRS}:{_existing_path}" if _existing_path else _HOMEBREW_LIB_DIRS
        )

from jinja2 import BaseLoader, Environment, select_autoescape

from schema import EstimateReport, ExtractedRenovationData, MaterialTier, WorkPhase

try:
    import weasyprint

    WEASYPRINT_IMPORT_ERROR: Exception | None = None
except (ImportError, OSError) as exc:
    # ImportError: package not installed at all. OSError: installed but the native
    # Pango/GLib/cairo libraries could not be dlopen'd (see module docstring). Either way,
    # `generate_estimate_html` still works - only `generate_estimate_pdf` needs this.
    weasyprint = None  # type: ignore[assignment]
    WEASYPRINT_IMPORT_ERROR = exc


# --------------------------------------------------------------------------------------
# Polish display labels (client-facing document - always Polish, per master prompt's own
# "Kosztorys Budowlany" naming). Kept as small lookup tables here, never in the LLM prompt.
# --------------------------------------------------------------------------------------

TIER_LABELS: dict[MaterialTier, str] = {
    MaterialTier.ECONOMY: "Ekonomiczny",
    MaterialTier.STANDARD: "Standardowy",
    MaterialTier.PREMIUM: "Premium",
}

PHASE_LABELS: dict[WorkPhase, str] = {
    WorkPhase.DEMOLITION: "Demontaż",
    WorkPhase.ROUGH_MEP: "Instalacje (elektryka/hydraulika)",
    WorkPhase.SCREED: "Wylewka/jastrych",
    WorkPhase.PLASTER: "Tynkowanie",
    WorkPhase.FINISH: "Wykończenie",
    WorkPhase.ENGINEERING: "Instalacje grzewcze/wentylacja",
    WorkPhase.FACADE_ROOF: "Elewacja/dach",
}

FACTOR_TYPE_LABELS: dict[str, str] = {
    "complexity": "Złożoność",
    "waste": "Zapas materiału",
    "logistics": "Logistyka",
    "seasonal": "Sezonowość",
}

PRECISION_LABELS: dict[str, str] = {
    "low": "Orientacyjny (LOW)",
    "mid": "Standardowy (MID)",
    "high": "Szczegółowy (HIGH)",
    "expert_required": "Wymaga eksperta",
}


def _format_money(amount: Decimal, currency: str) -> str:
    """Polish money formatting: space as thousands separator, comma as decimal point,
    e.g. Decimal('1234.5') -> '1 234,50 PLN'."""
    quantized = amount.quantize(Decimal("0.01"))
    text = f"{quantized:,.2f}"
    text = text.replace(",", "\u00a0").replace(".", ",")
    return f"{text} {currency}"


def _format_percent(rate: Decimal) -> str:
    """e.g. Decimal('0.15') -> '15%'."""
    return f"{(rate * 100).quantize(Decimal('1'))}%"


_ENV = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html"]))
_ENV.filters["money"] = _format_money
_ENV.filters["percent"] = _format_percent
_ENV.filters["phase_label"] = lambda phase: PHASE_LABELS.get(phase, str(phase))
_ENV.filters["tier_label"] = lambda tier: TIER_LABELS.get(tier, str(tier))
_ENV.filters["factor_label"] = lambda ft: FACTOR_TYPE_LABELS.get(ft, ft)
_ENV.filters["precision_label"] = lambda level: PRECISION_LABELS.get(
    level.value if hasattr(level, "value") else level, str(level)
)


_TEMPLATE_SOURCE = """
<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>Kosztorys Budowlany</title>
<style>
    @page { size: A4; margin: 2cm 1.5cm; }
    body { font-family: 'DejaVu Sans', Arial, sans-serif; font-size: 10pt; color: #1a1a1a; }
    h1 { font-size: 18pt; margin-bottom: 0; }
    h2 { font-size: 13pt; margin-top: 1.4em; margin-bottom: 0.4em; border-bottom: 2px solid #2b3a4a; padding-bottom: 2px; }
    .subtitle { color: #555; margin-top: 2px; margin-bottom: 1.2em; }
    .badge { display: inline-block; background: #2b3a4a; color: #fff; padding: 3px 10px; border-radius: 3px; font-size: 9pt; }
    .disclaimer { background: #fff6e0; border: 1px solid #e0c068; padding: 8px 12px; margin: 8px 0; font-size: 9pt; }
    .handoff-box { background: #fdecea; border: 1px solid #d9534f; padding: 16px; margin-top: 20px; font-size: 11pt; }
    table { width: 100%; border-collapse: collapse; margin-bottom: 10px; }
    th, td { border: 1px solid #ccc; padding: 4px 6px; text-align: left; font-size: 9pt; }
    th { background: #eef1f4; }
    td.num, th.num { text-align: right; }
    .tiers-table th { text-align: center; }
    .tiers-table td.num { text-align: right; }
    ul.exclusions { padding-left: 18px; font-size: 9pt; }
    .footer { margin-top: 24px; font-size: 8pt; color: #777; border-top: 1px solid #ccc; padding-top: 6px; }
    .total-row { font-weight: bold; background: #eef1f4; }
</style>
</head>
<body>

<h1>Kosztorys Budowlany</h1>
<div class="subtitle">
    {% if contractor_profile %}{{ contractor_profile.company_name }} &middot; {% endif %}
    wygenerowano {{ generated_at }}
    {% if rooms %} &middot; {{ rooms | join(", ") }}{% endif %}
    {% if total_area_m2 %} &middot; {{ total_area_m2 }} m&sup2;{% endif %}
</div>
<span class="badge">{{ precision_level | precision_label }}</span>

{% if requires_expert_handoff %}
<div class="handoff-box">
    <strong>Ten obiekt wymaga udziału eksperta.</strong><br>
    {{ expert_handoff_message }}
</div>
{% else %}

{% if disclaimer %}
<div class="disclaimer">{{ disclaimer }}</div>
{% endif %}

{% if line_items %}
<h2>Zakres prac</h2>
<table>
    <thead>
        <tr>
            <th>Pomieszczenie</th>
            <th>Zakres prac</th>
            <th class="num">Ilość</th>
            <th class="num">Robocizna</th>
            <th class="num">Materiał</th>
            <th>Zastosowane czynniki</th>
        </tr>
    </thead>
    <tbody>
        {% for item in line_items %}
        <tr>
            <td>{{ item.work_item.room or "-" }}</td>
            <td>{{ item.work_item.work_type }}{% if item.work_item.phase %} ({{ item.work_item.phase | phase_label }}){% endif %}</td>
            <td class="num">{% if item.work_item.quantity %}{{ item.work_item.quantity }} {{ item.work_item.unit or "" }}{% else %}-{% endif %}</td>
            <td class="num">{{ item.labor_cost | money(currency) }}</td>
            <td class="num">{{ item.material_cost | money(currency) }}</td>
            <td>
                {% for factor in item.applied_factors %}
                    {{ factor.name }} ({{ factor.factor_type | factor_label }}: {{ factor.multiplier | percent }})<br>
                {% endfor %}
            </td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endif %}

{% if project_level_factors %}
<h2>Dopłaty projektowe (logistyka, sezon)</h2>
<table>
    <thead><tr><th>Czynnik</th><th class="num">Stawka</th><th class="num">Kwota</th></tr></thead>
    <tbody>
        {% for factor in project_level_factors %}
        <tr>
            <td>{{ factor.name }} ({{ factor.factor_type | factor_label }})</td>
            <td class="num">{{ factor.multiplier | percent }}</td>
            <td class="num">{{ factor.amount | money(currency) }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endif %}

{% if fixed_overheads %}
<h2>Koszty stałe</h2>
<table>
    <tbody>
        {% for label, amount in fixed_overheads.items() %}
        <tr><td>{{ label }}</td><td class="num">{{ amount | money(currency) }}</td></tr>
        {% endfor %}
    </tbody>
</table>
{% endif %}

<h2>Podsumowanie kosztów</h2>
{% if cost_breakdowns | length == 3 %}
<table class="tiers-table">
    <thead>
        <tr>
            <th></th>
            {% for cb in cost_breakdowns %}<th>{{ cb.tier | tier_label }}</th>{% endfor %}
        </tr>
    </thead>
    <tbody>
        <tr><td>Robocizna</td>{% for cb in cost_breakdowns %}<td class="num">{{ cb.labor_cost | money(currency) }}</td>{% endfor %}</tr>
        <tr><td>Materiały</td>{% for cb in cost_breakdowns %}<td class="num">{{ cb.material_cost | money(currency) }}</td>{% endfor %}</tr>
        <tr><td>Suma częściowa</td>{% for cb in cost_breakdowns %}<td class="num">{{ cb.subtotal | money(currency) }}</td>{% endfor %}</tr>
        <tr><td>Rezerwa ryzyka ({{ cost_breakdowns[0].risk_coefficient | percent }})</td>{% for cb in cost_breakdowns %}<td class="num">{{ cb.risk_buffer_amount | money(currency) }}</td>{% endfor %}</tr>
        <tr><td>VAT ({{ cost_breakdowns[0].tax_rate | percent }})</td>{% for cb in cost_breakdowns %}<td class="num">{{ cb.tax_amount | money(currency) }}</td>{% endfor %}</tr>
        <tr class="total-row"><td>RAZEM</td>{% for cb in cost_breakdowns %}<td class="num">{{ cb.total | money(currency) }}</td>{% endfor %}</tr>
    </tbody>
</table>
{% else %}
{% for cb in cost_breakdowns %}
<table>
    <tbody>
        <tr><td>Robocizna</td><td class="num">{{ cb.labor_cost | money(currency) }}</td></tr>
        <tr><td>Materiały</td><td class="num">{{ cb.material_cost | money(currency) }}</td></tr>
        <tr><td>Suma częściowa</td><td class="num">{{ cb.subtotal | money(currency) }}</td></tr>
        <tr><td>Rezerwa ryzyka ({{ cb.risk_coefficient | percent }})</td><td class="num">{{ cb.risk_buffer_amount | money(currency) }}</td></tr>
        <tr><td>VAT ({{ cb.tax_rate | percent }})</td><td class="num">{{ cb.tax_amount | money(currency) }}</td></tr>
        <tr class="total-row"><td>RAZEM</td><td class="num">{{ cb.total | money(currency) }}</td></tr>
    </tbody>
</table>
{% endfor %}
{% endif %}

{% if design_service_cost is not none %}
<h2>Usługa projektowa (osobno od budowy)</h2>
<table>
    <tbody>
        <tr><td>Koszt projektu wnętrza</td><td class="num">{{ design_service_cost | money(currency) }}</td></tr>
    </tbody>
</table>
{% endif %}

{% if phase_schedule %}
<h2>Harmonogram robót</h2>
<table>
    <thead><tr><th>Etap</th><th class="num">Start (dzień)</th><th class="num">Czas pracy (dni)</th><th class="num">Schnięcie/wiązanie (dni)</th><th class="num">Koniec (dzień)</th></tr></thead>
    <tbody>
        {% for phase in phase_schedule %}
        <tr>
            <td>{{ phase.phase | phase_label }}</td>
            <td class="num">{{ phase.starts_after_day }}</td>
            <td class="num">{{ phase.work_duration_days }}</td>
            <td class="num">{{ phase.curing_days }}</td>
            <td class="num">{{ phase.ends_on_day }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% if estimated_duration_days is not none %}
<p><strong>Szacowany czas realizacji: {{ estimated_duration_days }} dni</strong></p>
{% endif %}
{% endif %}

{% if exclusions %}
<h2>Co NIE wchodzi w zakres kosztorysu</h2>
<ul class="exclusions">
    {% for item in exclusions %}<li>{{ item }}</li>{% endfor %}
</ul>
{% endif %}

{% if contractor_profile and (contractor_profile.payment_schedule or contractor_profile.warranty_terms) %}
<h2>Warunki umowy</h2>
{% if contractor_profile.payment_schedule %}
<table>
    <thead><tr><th>Etap płatności</th><th class="num">Udział</th><th>Warunek</th></tr></thead>
    <tbody>
        {% for milestone in contractor_profile.payment_schedule %}
        <tr><td>{{ milestone.label }}</td><td class="num">{{ milestone.percent | percent }}</td><td>{{ milestone.trigger }}</td></tr>
        {% endfor %}
    </tbody>
</table>
{% endif %}
{% if contractor_profile.warranty_terms %}
<table>
    <thead><tr><th>Rodzaj prac</th><th class="num">Gwarancja (miesiące)</th></tr></thead>
    <tbody>
        {% for term in contractor_profile.warranty_terms %}
        <tr><td>{{ term.work_category }}</td><td class="num">{{ term.warranty_months }}</td></tr>
        {% endfor %}
    </tbody>
</table>
{% endif %}
{% endif %}

{% endif %}

<div class="footer">
    Kosztorys wygenerowany automatycznie na podstawie podanych danych - {{ generated_at }}.
</div>

</body>
</html>
"""

_TEMPLATE = _ENV.from_string(_TEMPLATE_SOURCE)


def generate_estimate_html(
    report: EstimateReport,
    data: ExtractedRenovationData,
    generated_at: datetime | None = None,
) -> str:
    """Render the full Kosztorys Budowlany document as an HTML string. Pure function, no
    native dependencies - safe to unit-test everywhere `jinja2` is installed.
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    return _TEMPLATE.render(
        generated_at=generated_at.strftime("%d.%m.%Y"),
        currency=data.currency,
        rooms=data.rooms,
        total_area_m2=data.total_area_m2,
        precision_level=report.precision_level,
        requires_expert_handoff=report.requires_expert_handoff,
        expert_handoff_message=report.expert_handoff_message,
        disclaimer=report.disclaimer,
        line_items=report.line_items,
        project_level_factors=report.project_level_factors,
        fixed_overheads=report.fixed_overheads,
        cost_breakdowns=report.cost_breakdowns,
        design_service_cost=report.design_service_cost,
        phase_schedule=report.phase_schedule,
        estimated_duration_days=report.estimated_duration_days,
        exclusions=report.exclusions,
        contractor_profile=report.contractor_profile,
    )


def generate_estimate_pdf(
    report: EstimateReport,
    data: ExtractedRenovationData,
    generated_at: datetime | None = None,
) -> bytes:
    """Render the document and convert it to PDF bytes via WeasyPrint. Requires the native
    Pango/GLib/cairo libraries to be importable (see module docstring); raises `RuntimeError`
    with a clear message if they aren't, rather than a confusing WeasyPrint traceback.
    """
    if weasyprint is None:
        raise RuntimeError(
            "WeasyPrint is not usable in this environment (native Pango/GLib/cairo libraries "
            f"could not be loaded): {WEASYPRINT_IMPORT_ERROR}. See pdf_generator.py's module "
            "docstring for the macOS fix, or install the equivalent Linux system packages."
        )
    html = generate_estimate_html(report, data, generated_at=generated_at)
    return weasyprint.HTML(string=html).write_pdf()


def save_estimate_pdf(
    report: EstimateReport,
    data: ExtractedRenovationData,
    output_path: str | Path,
    generated_at: datetime | None = None,
) -> Path:
    """Convenience wrapper: render + write the PDF to `output_path`, returning the `Path`."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(generate_estimate_pdf(report, data, generated_at=generated_at))
    return output_path
