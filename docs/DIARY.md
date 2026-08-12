# Build Diary — Kosztorys-Engine (source of truth)

> Purpose: avoid re-deriving decisions, re-reading old prompts, or redoing work. This file is
> updated **every time a module is completed and its regression tests pass**. If it's not
> written here as "done", assume it is NOT done, even if a master prompt describes it.

Environment: Python 3.10+ required (venv at `.venv/`, see `/memories/repo/conventions.md`).

---

## Status matrix (module → state)

| Module | State | Tests | Notes |
|---|---|---|---|
| `schema.py` | ✅ done (v1 + v2 merged, + small additions for calculator inputs) | ⬜ smoke-tested indirectly via test_calculator.py | see below |
| `calculator.py` | ✅ done (v1 §4 + v2 §1,2,5,6) | ✅ 20/20 passing (`tests/test_calculator.py`) | Uses a `PriceRepositoryProtocol` seam - no real DB yet |
| `tests/test_calculator.py` | ✅ done | ✅ 20 passed | Golden-value regression anchors - see conclusion below |
| `db/models.py` | ⬜ not started | ⬜ | v2 §5 LogisticsFactor/SeasonalFactor tables pending; must implement `PriceRepositoryProtocol` for real |
| `price_repository.py` | ⬜ not started | ⬜ | |
| `llm_parser.py` | ⬜ not started | ⬜ | v2 §3 heritage detection, §4 design-service question, §8 tone — pending |
| `pdf_generator.py` | ⬜ not started | ⬜ | v2 §2 exclusions block, §6 3-tier columns, §7 contract block — pending |
| `messengers/base.py` | ⬜ not started | ⬜ | |
| `messengers/telegram_adapter.py` | ⬜ not started | ⬜ | v1 scope: Telegram only for production DoD |
| `messengers/whatsapp_adapter.py`, `viber_adapter.py` | ⬜ not started | — | stub-only for v1 |
| `core/dialog_manager.py` | ⬜ not started | ⬜ | |
| `config.py` | ⬜ not started | ⬜ | |
| `app.py` | ⬜ not started | ⬜ | |
| CI (GitHub Actions), Dockerfile, docker-compose | ⬜ not started | — | |
| `docs/USER_MANUAL.md` | ✅ created, updated alongside features | — | |

---

## Master prompt v2 coverage checklist (as of this entry)

Answering directly: **only §1's schema-level groundwork is done. §2, §3, §4, §5, §6, §7, §8 are
NOT implemented yet** — they require `calculator.py`, `llm_parser.py`, `db/models.py`, and
`pdf_generator.py`, none of which exist yet.

| v2 section | What it requires | Status |
|---|---|---|
| §1 Phases & duration | `WorkPhase`, `WorkItem.phase/depends_on/curing_days` in schema; phase graph + `estimated_duration_days` in calculator | Schema: ✅ &nbsp;/&nbsp; Calculator graph: ✅ (topological sort over phases, tested with a screed+curing scenario) |
| §2 Risk buffer + exclusions | Risk formula in calculator; auto-generated exclusions list in PDF | Schema: ✅ &nbsp;/&nbsp; Calculator risk formula: ✅ &nbsp;/&nbsp; Exclusions list: ✅ (generated in calculator, in Polish) &nbsp;/&nbsp; Rendered in PDF: ⬜ (pdf_generator.py not started) |
| §3 Expert-required (heritage) | `EXPERT_REQUIRED` enum value; keyword detection in llm_parser; handoff message + notify-admin trigger | Schema enum: ✅ &nbsp;/&nbsp; Calculator short-circuit (no pricing) + handoff message: ✅ &nbsp;/&nbsp; Keyword detection (llm_parser) + admin notify (dialog_manager): ⬜ |
| §4 Design service | `DesignServiceType`, `DesignServiceRequest`, separate pricing, explicit dialogue question | Schema: ✅ &nbsp;/&nbsp; Calculator pricing (3 modes, always separate from construction total): ✅ &nbsp;/&nbsp; llm_parser dialogue question: ⬜ |
| §5 Logistics/seasonal factors | `LogisticsFactor`/`SeasonalFactor` DB tables; applied as itemized labor surcharges | Schema (`AppliedFactor`): ✅ &nbsp;/&nbsp; Calculator application via `PriceRepositoryProtocol`: ✅ (tested) &nbsp;/&nbsp; Real DB tables (`db/models.py`): ⬜ |
| §6 Three material tiers | `MaterialTier`, per-tier `CostBreakdown` | Schema: ✅ &nbsp;/&nbsp; Calculator producing 3 tiers with shared labor cost: ✅ (tested) &nbsp;/&nbsp; PDF columns: ⬜ |
| §7 Contract block in PDF | Payment schedule, warranty terms, exclusions in the rendered PDF | Schema (`ContractorProfile`, `PaymentMilestone`, `WarrantyTerm`): ✅ &nbsp;/&nbsp; Calculator attaches `contractor_profile` to report: ✅ &nbsp;/&nbsp; PDF rendering: ⬜ |
| §8 "Voice of the foreman" tone | System prompt style for clarifying questions | ⬜ not started (lives in `llm_parser.py`) |

**Conclusion: proceed to `db/models.py` + `price_repository.py` next** — this is the real,
Postgres-backed implementation of `PriceRepositoryProtocol` that `calculator.py` currently only
consumes via a test fake. After that: `llm_parser.py` (§3, §4, §8), then `pdf_generator.py` (§2, §6, §7).

---

## Entries

### 2026-08-12 — schema.py (v1 base + v2 domain extension)
- Implemented all v1 core models: `PrecisionLevelEnum`, `WorkItem`, `ExtractedRenovationData`,
  `CostBreakdown`, `EstimateLineItem`, `EstimateReport`.
- Merged in v2 domain extension in the same file (point-in-place, not a rewrite):
  `WorkPhase`, `MaterialTier`, `DesignServiceType`, `AppliedFactor`, `DesignServiceRequest`,
  `PaymentMilestone`, `WarrantyTerm`, `ContractorProfile`, `PhaseScheduleItem`;
  `PrecisionLevelEnum.EXPERT_REQUIRED`; `WorkItem.phase/depends_on/curing_days`;
  `ExtractedRenovationData.is_heritage_site/heritage_keywords_matched/design_service`;
  `CostBreakdown` reworked to the risk-buffer formula (`risk_coefficient`/`risk_buffer_amount`,
  `tier`); `EstimateReport` gained `cost_breakdowns[]`, `phase_schedule[]`,
  `estimated_duration_days`, `exclusions[]`, `design_service_cost`, `contractor_profile`,
  `requires_expert_handoff`, `expert_handoff_message`.
- Verified: constructed full LOW/EXPERT_REQUIRED examples + a full HIGH-precision report with
  3 tiers, phase schedule, and contractor profile — all validate with no errors.
- Environment fix: discovered system `python3` is 3.9.5 (incompatible with pydantic v2's
  `X | None` runtime evaluation) → created `.venv` with `/usr/local/bin/python3.10`.
- No regression suite yet for this module (pure data, nothing to regress until calculator.py
  consumes it — first tests will be `tests/test_schema.py` smoke tests + `tests/test_calculator.py`
  golden values that exercise these models together).

### 2026-08-12 — calculator.py + tests/test_calculator.py
- Implemented `EstimateCalculator(data, prices)` as a pure class (no I/O/side effects), plus
  `PriceRepositoryProtocol` — a `typing.Protocol` seam so calculator.py doesn't need `db/models.py`
  or `price_repository.py` to exist yet. Real Postgres-backed implementation is the next step.
- Implemented: labor/material cost formula with complexity & waste factors (v1 §4); accessory
  consumables auto-derived from a base item's quantity via `AccessoryRule` (v1 §4);
  `compute_wall_area` helper (v1 §4); risk-buffer formula with baseline-from-repo +
  old-building/hidden-conditions +0.05 addons (v2 §2); auto-generated Polish exclusions list
  incl. the asbestos warning (v2 §2 + foreman suggestion #1); phase dependency graph via
  topological sort producing `phase_schedule` + `estimated_duration_days`, respecting
  `curing_days` (v2 §1); logistics/seasonal surcharges applied to labor cost and reported as
  `project_level_factors` (v2 §5); three-tier `cost_breakdowns` for HIGH precision with shared
  labor cost across tiers (v2 §6); separate `design_service_cost` pricing for all 3
  `DesignServiceType` modes, validated to raise if a required numeric field is missing (v2 §4);
  `EXPERT_REQUIRED` short-circuit returning zero pricing + a Polish handoff message (v2 §3) —
  note: actually notifying an admin/human is `core/dialog_manager.py`'s job, not calculator.py's.
- Small schema.py amendments needed to support the above (point-in-place, not a rewrite):
  `ExtractedRenovationData.hidden_conditions_unknown/floor_number/has_elevator/estimate_month`;
  `EstimateReport.project_level_factors/fixed_overheads`.
- Added `requirements.txt` (`pydantic>=2`) and `requirements-dev.txt` (`+pytest>=8`).
- **Tests: 20/20 passing** in `tests/test_calculator.py`, using an in-memory `FakePriceRepository`
  — no DB dependency. Covers LOW/MID/HIGH precision, EXPERT_REQUIRED, risk-coefficient addons,
  phase scheduling with curing days, logistics/seasonal surcharges, accessory derivation,
  large-format-tile & old-building complexity factors, and all 3 design-service pricing modes.
  These are now the **golden-value regression anchors** for the pricing engine — see the file
  header for the update policy.

---

## Foreman's Suggestions Log
*(30-years-on-site perspective — logged as they come up, not all necessarily built yet; each
entry says whether it's already implemented, planned, or just a flagged idea for later.)*

1. **[implemented]** Pre-1970 buildings + demolition → mandatory **asbestos-check disclaimer**
   in `calculator.py`'s auto-generated exclusions list. Old floor tile adhesive, roofing sheets
   and pipe insulation from that era very often contain asbestos in PL/CEE housing stock —
   pricing demolition without this warning is how contractors get hit with reopened complaints
   later. See `EstimateCalculator._build_exclusions`, tested in
   `test_exclusions_include_asbestos_warning_for_old_building`.
2. **[idea]** Any change to load-bearing walls (`demolition` phase + a "wall" note mentioning
   "nośna"/"load-bearing") should force `EXPERT_REQUIRED`-style handoff too, same as heritage —
   removing a bearing wall without a structural engineer's sign-off is a legal liability, not
   just a technical one. Consider generalizing the "hard-stop, no auto-price" mechanism beyond
   heritage sites.
3. **[idea]** Add a clarifying question about **who pays for the skip/waste container and
   permit** ("wywóz gruzu") on any renovation >~10m² of demolition — clients are frequently
   surprised by this as a separate line, and it varies by city (Warsaw needs a street permit for
   a container on a public road).
4. **[idea]** v2 §5's `SeasonalFactor.wet_process_allowed` is more than a price multiplier — a
   real foreman won't run screed/plaster/plastering ("mokre procesy") in an unheated site during
   winter at any price, because it won't cure properly and the work will fail. Right now
   `calculator.py` only applies `demand_multiplier` as a cost surcharge; it does NOT yet block or
   push out the `phase_schedule` when a wet-process phase (SCREED/PLASTER) lands in a
   `wet_process_allowed=False` month. Flagging for when `db/models.py`'s `SeasonalFactor` table
   and the phase-schedule logic meet — should probably shift the phase's start date to the next
   allowed month rather than just pricing it, and surface a clear warning to the client.
