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
| `db/models.py` | ✅ done | ✅ exercised via test_price_repository.py | SQLAlchemy 2.x async ORM, Postgres+asyncpg; Alembic migrations in `alembic/` |
| `price_repository.py` | ✅ done | ✅ 16/16 passing (`tests/test_price_repository.py`, real Postgres) | Real `PriceRepositoryProtocol` impl - loads full catalog once (async), serves sync lookups. Fails loudly on missing price/tax/risk/contractor data via `PriceNotFoundError` |
| `llm_parser.py` | ✅ done | ✅ 24/24 passing (`tests/test_llm_parser.py`, fake `completion_fn`, no live LLM calls) | v2 §3 heritage detection (deterministic, pre-LLM short-circuit), §4 design-service question constant + merge helper, §8 foreman tone system prompt. LLM never assigns `precision_level`/`is_heritage_site` — both are pure functions in this module |
| `pdf_generator.py` | ✅ done | ✅ 10/10 passing (`tests/test_pdf_generator.py`; 9 pure HTML tests + 1 real-PDF-bytes test, skips gracefully without WeasyPrint's native libs) | v2 §2 exclusions, §6 3-tier columns, §7 contract block. Jinja2 (pure, always testable) + WeasyPrint (needs native Pango/GLib/cairo) |
| `messengers/base.py` | ✅ done | ✅ 7/7 passing (`tests/test_messengers_base.py`) | Defines `InboundMessage` + abstract `MessengerAdapter` (v1 §2). Pure interface, no platform SDKs yet |
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
| §2 Risk buffer + exclusions | Risk formula in calculator; auto-generated exclusions list in PDF | Schema: ✅ &nbsp;/&nbsp; Calculator risk formula: ✅ &nbsp;/&nbsp; Exclusions list: ✅ (generated in calculator, in Polish) &nbsp;/&nbsp; Rendered in PDF: ✅ (`pdf_generator.py`'s "Co NIE wchodzi w zakres kosztorysu" section) |
| §3 Expert-required (heritage) | `EXPERT_REQUIRED` enum value; keyword detection in llm_parser; handoff message + notify-admin trigger | Schema enum: ✅ &nbsp;/&nbsp; Calculator short-circuit (no pricing) + handoff message: ✅ &nbsp;/&nbsp; Keyword detection (llm_parser, bilingual RU/PL, short-circuits before any LLM call): ✅ &nbsp;/&nbsp; admin notify (dialog_manager, will call `MessengerAdapter.send_text`): ⬜ |
| §4 Design service | `DesignServiceType`, `DesignServiceRequest`, separate pricing, explicit dialogue question | Schema: ✅ &nbsp;/&nbsp; Calculator pricing (3 modes, always separate from construction total): ✅ &nbsp;/&nbsp; llm_parser `DESIGN_SERVICE_QUESTION` constant + `merge_design_service_answer`: ✅ (extraction-time seam only) &nbsp;/&nbsp; actual multi-turn dialogue: ⬜ (needs `core/dialog_manager.py`) |
| §5 Logistics/seasonal factors | `LogisticsFactor`/`SeasonalFactor` DB tables; applied as itemized labor surcharges | Schema (`AppliedFactor`): ✅ &nbsp;/&nbsp; Calculator application via `PriceRepositoryProtocol`: ✅ (tested) &nbsp;/&nbsp; Real DB tables (`db/models.py`): ✅ (tested against real Postgres) - **note:** `wet_process_allowed` column exists but is not yet consumed by the phase scheduler, see Foreman's Suggestion #4 |
| §6 Three material tiers | `MaterialTier`, per-tier `CostBreakdown` | Schema: ✅ &nbsp;/&nbsp; Calculator producing 3 tiers with shared labor cost: ✅ (tested) &nbsp;/&nbsp; PDF columns: ✅ (`pdf_generator.py`'s three-column `tiers-table`, shared labor row) |
| §7 Contract block in PDF | Payment schedule, warranty terms, exclusions in the rendered PDF | Schema (`ContractorProfile`, `PaymentMilestone`, `WarrantyTerm`): ✅ &nbsp;/&nbsp; Calculator attaches `contractor_profile` to report: ✅ &nbsp;/&nbsp; PDF rendering: ✅ ("Warunki umowy" section) |
| §8 "Voice of the foreman" tone | System prompt style for clarifying questions | ✅ done — `SYSTEM_PROMPT` in `llm_parser.py` (Polish, plain-language, explicitly forbids the LLM from pricing) + `CLARIFYING_QUESTION_BY_FIELD`/`LOW_PRECISION_CLARIFYING_QUESTIONS` avoid jargon like "wastage factor" |

**Conclusion: proceed to `messengers/telegram_adapter.py` next** — the channel-agnostic contract
(`InboundMessage`, `MessengerAdapter`) is now defined and tested via an in-memory fake adapter.
Next per the master prompt's own generation order (section 6, step 6): the Telegram adapter
(aiogram 3.x, v1 scope: Telegram only for production DoD), then `whatsapp_adapter.py`/
`viber_adapter.py` as stubs, then `core/dialog_manager.py` to wire parser → calculator → PDF →
messenger together.

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

### 2026-08-12 — db/models.py + price_repository.py + Alembic
- `db/models.py`: SQLAlchemy 2.x async ORM (Postgres + `asyncpg`), one table per
  `PriceRepositoryProtocol` concern: `LaborRate`, `MaterialPrice`, `WasteFactor`,
  `ComplexityRule` (generalizes "large-format tile"/"old-building" into a data-driven
  `(work_type, condition) -> multiplier` table), `AccessoryRuleModel`, `LogisticsFactor` +
  `SeasonalFactor` (v2 §5, table shapes taken verbatim from the master prompt), `FixedOverhead`,
  `RiskBaseline`, `TaxRate`, `ContractorProfileModel` + `PaymentMilestoneModel` +
  `WarrantyTermModel` (single-contractor for v1).
- `db/session.py`: async engine/session factory reading `DATABASE_URL` from the environment
  (no hardcoded default — fails loudly if unset and actually used).
- `price_repository.py`: `PriceRepository` — a **synchronous** `PriceRepositoryProtocol`
  implementation, deliberately not `async def`, because `calculator.py` is pure/sync and calls
  repository methods many times per estimate. `load_price_repository(session)` does ONE async
  pass over every table and returns an in-memory snapshot; call it once per request (or on a
  short TTL) rather than per-lookup. Fail-fast policy: missing labor rate / material price /
  tax rate / risk baseline / contractor profile raises `PriceNotFoundError` (never silently
  guesses a price); missing waste/complexity/logistics/seasonal/accessory rows safely default
  to "no effect" since those are additive refinements, not the base price.
- Alembic wired up (`alembic/`, async template): `env.py` points `target_metadata` at
  `db.models.Base.metadata` and reads `DATABASE_URL` from the environment (never commits a real
  connection string). First migration `initial_price_catalog_schema` generated and verified to
  apply cleanly (`alembic upgrade head`) on an empty Postgres — all 14 tables created correctly.
- **Tests: 16/16 passing** in `tests/test_price_repository.py`, run against a REAL local
  Postgres 16 container (not mocked) — round-trips every repository method, the fail-fast
  `PriceNotFoundError` paths, and one full end-to-end `EstimateCalculator` run using the
  DB-loaded repository (proves it's a faithful drop-in replacement for the test fake). Marked
  `@pytest.mark.integration`; `tests/conftest.py` auto-skips this file (not errors) when no
  Postgres is reachable at `TEST_DATABASE_URL` — verified both the "DB up" and "DB down" paths.
  **How to run locally:**
  ```
  docker run --rm -d --name kosztorys_test_pg -e POSTGRES_PASSWORD=test \
      -e POSTGRES_DB=kosztorys_test -p 55432:5432 postgres:16-alpine
  source .venv/bin/activate && python -m pytest tests/ -v
  ```
- Updated `requirements.txt` (+sqlalchemy, asyncpg, greenlet, alembic) and
  `requirements-dev.txt` (+pytest-asyncio).

### 2026-08-12 — llm_parser.py + tests/test_llm_parser.py
- Implemented `parse_renovation_request(raw_text, photo_notes, completion_fn)` — the single
  entry point turning free text (+ optional photo notes) into a fully-typed
  `ExtractedRenovationData`. Provider selection is purely `LLM_MODEL` env var via
  `litellm.completion()` (`default_completion_fn`); every other function takes an injectable
  `CompletionFn`, so `tests/test_llm_parser.py` never makes a live API call.
- **Heritage detection (v2 §3)**: `detect_heritage_keywords` — bilingual RU/PL substring list
  (памятник/лепнина/реставрац.../zabytek/konserwator zabytków/sztukateria/fresk...) — runs
  BEFORE any LLM call. A match short-circuits straight to `build_heritage_handoff_data` →
  `EXPERT_REQUIRED`, empty `work_items`, zero LLM cost. The LLM is never asked to judge
  heritage status; it's a deterministic pre-filter.
- **`_LLMExtractedFields`**: the narrow pydantic schema the LLM is allowed to populate
  (facts only — country/currency/city/rooms/area/is_old_building/work_items (raw dicts, for
  phase post-processing)/design_service/hidden_conditions_unknown/floor/elevator/month).
  Deliberately excludes `precision_level`/`is_heritage_site`/`missing_fields`/
  `clarifying_questions` — those are always computed by this module, never left to the LLM.
- **`extract_fields_via_llm`**: calls the LLM once; on invalid JSON or a schema violation,
  retries ONCE with the error fed back into the prompt; raises `LLMParsingError` on a second
  failure (fail loudly, never fabricate facts). Tolerates markdown code-fenced JSON responses.
- **`assign_precision_level`** (pure function, v1 §3): LOW when `work_items` is empty or any
  item's `work_type` ends with `_generic` (matches schema.py's own LOW example verbatim); MID
  when named trades are missing per-work-type detail fields (`REQUIRED_HIGH_PRECISION_FIELDS`,
  e.g. tiling needs material/tile_size_cm/layout_pattern, demolition needs
  substrate_condition) or `is_old_building` is unknown; HIGH when nothing blocking is missing.
  `CLARIFYING_QUESTION_BY_FIELD` maps each gap to a plain-language Polish question (max 5
  returned, per schema.py's own field docstring).
- **Phase inference**: if the LLM's work_item dict omits `phase`, `infer_default_phase` guesses
  from keywords in `work_type` (demolition/screed/plaster/electrical/plumbing/facade/roof/
  heating/ventilation → matching `WorkPhase`, else FINISH) rather than failing the whole
  extraction over one cosmetic gap. A genuinely missing `work_type` still fails validation
  (wrapped as `LLMParsingError`, not silently dropped).
- **Design service seam (v2 §4)**: `DESIGN_SERVICE_QUESTION` (Polish, foreman tone) +
  `needs_design_service_clarification`/`merge_design_service_answer` — extraction-time only;
  the actual multi-turn "did you answer yet" conversation state belongs in the not-yet-built
  `core/dialog_manager.py`, kept out of this stateless module on purpose.
- **Foreman tone (v2 §8)**: `SYSTEM_PROMPT` (Polish) explicitly tells the LLM it is a foreman,
  not an estimator, forbids it from computing any numbers, and frames the client's text as data
  to analyze rather than instructions to follow (prompt-injection defense, paired with
  `sanitize_raw_text`'s length cap and the delimited `---BEGIN/END CLIENT TEXT---` block in
  `_build_user_prompt`).
- **Tests: 24/24 passing** in `tests/test_llm_parser.py`, zero live LLM calls (fake
  `completion_fn` injected everywhere) — covers heritage short-circuit (RU+PL, no-LLM-call
  assertion), retry-once-on-malformed-JSON (success and both-attempts-fail paths), code-fence
  stripping, phase inference, precision-level assignment (table-driven LOW/MID/HIGH incl. the
  is_old_building-unknown case), design-service merge helper (proven pure/non-mutating), and
  full `parse_renovation_request` end-to-end for LOW and HIGH precision.
- **Full suite: 60/60 passing** (`tests/test_calculator.py` 20 + `tests/test_price_repository.py`
  16 + `tests/test_llm_parser.py` 24), confirmed against the local Postgres container.
- Added `litellm>=1.96` to `requirements.txt`.

### 2026-08-12 — pdf_generator.py + tests/test_pdf_generator.py
- Implemented per master prompt v1 section 5 ("Jinja2 + weasyprint") and v2 sections 2/6/7.
  Split into `generate_estimate_html` (pure Jinja2 rendering, zero native dependencies, fully
  unit-testable everywhere) and `generate_estimate_pdf`/`save_estimate_pdf` (need WeasyPrint's
  native Pango/GLib/cairo libraries to rasterize HTML → PDF) — mirrors the sync/async split
  already used for `price_repository.py`, this time for a "pure logic vs. needs a native lib"
  seam instead of a sync/async one.
- One Jinja2 template (embedded as a module string, Polish throughout) renders: line items
  with itemized `applied_factors` (v2 §5, "client sees WHY"), project-level logistics/seasonal
  surcharges, fixed overheads, cost summary (single table for LOW/MID, three-column
  economy/standard/premium table for HIGH per v2 §6, sharing one labor-cost row), a separate
  design-service line (v2 §4, never merged into the construction total), the phase schedule +
  total duration (v2 §1), the auto-generated "Co NIE wchodzi w zakres kosztorysu" exclusions
  list (v2 §2, verbatim from `report.exclusions` — nothing hand-written per estimate), and the
  contract block (payment schedule + warranty terms from `report.contractor_profile`, v2 §7).
  `EXPERT_REQUIRED` reports render ONLY the handoff message — no pricing sections at all,
  matching `calculator.py`'s own "must not produce a price" rule (v2 §3).
- **macOS/Apple-Silicon gotcha resolved**: this dev machine has both Intel Homebrew
  (`/usr/local`) and ARM Homebrew (`/opt/homebrew`) installed; `brew install pango gdk-pixbuf`
  via the Intel `brew` on the `PATH` installed x86_64 libs that failed to `dlopen` under the
  arm64 Python process ("incompatible architecture"). Fixed by installing via
  `/opt/homebrew/bin/brew` explicitly, then discovered WeasyPrint additionally needs
  `DYLD_FALLBACK_LIBRARY_PATH` to include `/opt/homebrew/lib` (macOS doesn't search Homebrew's
  lib dir for `dlopen` by default) — `pdf_generator.py` now sets this automatically at import
  time, guarded to `sys.platform == "darwin"` (a no-op on Linux prod/CI). Full gotcha logged in
  `/memories/repo/conventions.md` so it's never re-diagnosed from scratch.
- **Tests: 10/10 passing** in `tests/test_pdf_generator.py` — 9 pure-HTML tests (reusing
  `FakePriceRepository`/`_high_precision_data` from `tests/test_calculator.py` rather than
  duplicating fixtures) covering EXPERT_REQUIRED handoff-only rendering, the HIGH-precision
  three-tier table, exclusions, phase schedule/duration, the design-service section (present
  and absent), the contractor payment/warranty block, and the MID-precision single-summary
  table + mandatory disclaimer; plus 1 real-PDF-bytes test (`%PDF` magic-number check) that
  `pytest.mark.skipif`s gracefully if WeasyPrint's native libs aren't importable in the current
  environment (mirrors the DB-integration skip pattern from `tests/conftest.py`).
- Manually generated and visually inspected a full sample PDF (all sections populated at once:
  3 tiers, logistics + seasonal surcharges, design service, phase schedule, exclusions,
  contract block) via `pdftoppm` → PNG — confirmed clean two-page layout with no rendering bugs.
- **Full suite: 70/70 passing** (`tests/test_calculator.py` 20 + `tests/test_price_repository.py`
  16 + `tests/test_llm_parser.py` 24 + `tests/test_pdf_generator.py` 10).
- Added `jinja2>=3.1` and `weasyprint>=62` to `requirements.txt`.

### 2026-08-12 — messengers/base.py + tests/test_messengers_base.py
- Implemented per master prompt v1 section 2 ("мультиканальность"): `InboundMessage` (Pydantic
  model - `channel: Literal["telegram", "whatsapp", "viber"]`, `user_id`, and optional
  `text`/`voice_file_url`/`image_file_url`, independent of each other so a photo-with-caption
  is representable) and abstract `MessengerAdapter` (ABC) with `receive`/`send_text`/
  `send_document` as the single contract every channel adapter must implement. Pure interface
  module - no platform SDKs, no FastAPI router, no I/O; concrete adapters own all of that.
- `MessengerAdapter.channel: MessengerChannel` class attribute lets `app.py` (not yet built)
  identify/log by channel without importing each adapter module by name.
- **Tests: 7/7 passing** in `tests/test_messengers_base.py` - a minimal in-memory `FakeAdapter`
  (defined in the test file, not the module - real adapters get their own dedicated test
  files) proves: `MessengerAdapter` can't be instantiated directly (`TypeError`, ABC enforced),
  a concrete subclass's `receive`/`send_text`/`send_document` all work end-to-end,
  `InboundMessage` defaults optional fields to `None`, rejects an unknown `channel` value
  (`ValidationError`), and accepts voice/image URLs correctly.
- **Full suite: 77/77 passing** (previous 70 + `tests/test_messengers_base.py` 7). No new
  dependencies - `messengers/base.py` only uses `abc`, `pathlib`, `typing`, and `pydantic`
  (already in `requirements.txt`).

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
5. **[implemented]** A missing price in the catalog should never silently become a guessed
   number — a real estimator who doesn't know a price asks someone or leaves it blank, they
   don't make one up and hope. `price_repository.py`'s `PriceRepository` raises
   `PriceNotFoundError` for missing labor rate / material price / tax rate / risk baseline /
   contractor profile instead of defaulting, so a gap in the price catalog surfaces immediately
   as a loud error during estimate generation, not as a quietly-wrong number the client only
   discovers mid-renovation.6. **[idea]** `llm_parser.py`'s heritage keyword list is a fixed substring match — a real
   conservator would also flag buildings by **address/district** (e.g. Warsaw's Stare Miasto,
   Kraków's Kazimierz) even if the client never says the word "zabytek". Consider augmenting
   heritage detection with a city+district lookup table once `city`/address fields are more
   reliably extracted, rather than relying purely on the client happening to mention protection
   status in their own words.
7. **[idea]** Right now if the LLM infers a `phase` for a work item (because the client's text
   didn't make it obvious), that guess is silent — it doesn't show up anywhere in
   `missing_fields` or `clarifying_questions`. A foreman who wasn't 100% sure which phase a job
   belongs to would ask, not assume. Consider surfacing LLM-inferred-not-stated phases as a
   soft warning in the report ("we assumed X happens during finishing — let us know if that's
   wrong") once `pdf_generator.py`/`core/dialog_manager.py` exist to display it.
8. **[idea]** The rendered contract block currently shows whatever `payment_schedule`/
   `warranty_terms` a single `ContractorProfile` has, with no signature line or explicit
   "accepted by" section. A real signed kosztorys/umowa needs a place for both parties to sign
   and date, plus a contractor NIP/REGON (Polish business registry numbers) block — currently
   only `company_name` is modeled. Flagging as a v1 gap for whenever this PDF needs to become a
   legally binding attachment rather than just an informational quote.