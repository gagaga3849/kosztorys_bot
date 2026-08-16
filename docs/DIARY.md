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
| `messengers/telegram_adapter.py` | ✅ done | ✅ 8/8 passing (`tests/test_telegram_adapter.py`, `FakeBot` injected, no real Telegram API calls) | aiogram 3.x. `receive()` parses raw webhook dict directly (no aiogram Update model needed) |
| `messengers/whatsapp_adapter.py`, `viber_adapter.py` | ✅ done (stubs) | ✅ 11/11 passing (`tests/test_messenger_stub_adapters.py`) | Constructors validate credentials; every method raises a clear `NotImplementedError` - not implemented against a real Cloud API/Viber REST API yet (v1 scope: Telegram only) |
| `core/dialog_manager.py` | ✅ done | ✅ 12/12 passing (`tests/test_dialog_manager.py`, in-memory `FakeAdapter`, fake `completion_fn`, `tmp_path` for PDF output - no real Telegram/LLM/network calls) | Channel-agnostic conversation state machine: text-only gate, heritage/`EXPERT_REQUIRED` short-circuit + admin notify, design-service question/answer/retry loop, carries the answered `design_service` forward across refinement turns, `_finalize` renders+sends the PDF then any disclaimer/follow-up clarifying questions. **Known v1 gap:** `DEFAULT_DESIGN_FEE_PERCENT = Decimal("0.10")` is a hardcoded stopgap fee (see Foreman's Suggestion #10) since `calculator.py`'s `_price_design_service` requires a pre-populated fee and `PriceRepositoryProtocol` has no design-fee getter yet |
| `config.py` | ✅ done | ✅ exercised indirectly via `tests/test_app.py` (injection path never calls `Settings.from_env()`) | `Settings.from_env()` reads `TELEGRAM_BOT_TOKEN`/`DATABASE_URL` (required, fail-loud) + optional WhatsApp/Viber/admin/output-dir vars; validates `ADMIN_CHANNEL` against the `MessengerChannel` literal. Only called from `app.py`'s lifespan, never at import time |
| `app.py` | ✅ done (FINAL file per master prompt's generation order) | ✅ 7/7 passing (`tests/test_app.py`, `create_app(dialog_manager=...)` injection path, `fastapi.testclient.TestClient`, no real DB/network) | FastAPI app mounting `/webhook/telegram`, `/webhook/whatsapp`, `/webhook/viber`, `/healthz`. Telegram webhook verifies `X-Telegram-Bot-Api-Secret-Token` via `secrets.compare_digest` when `TELEGRAM_WEBHOOK_SECRET` is set; unconfigured/stub channels return 501; unexpected processing errors are logged server-side and still return 200 (never leak a stack trace, never trigger sender retry storms) |
| CI (GitHub Actions), Dockerfile, docker-compose | ✅ done | ✅ Dockerfile build+run smoke-tested locally (real `docker build`/`docker compose up`, healthz + a real heritage webhook payload processed end-to-end); CI workflow validated locally against a fresh (non-reused) Postgres container | Multi-stage `Dockerfile` (non-root user, WeasyPrint native libs, `HEALTHCHECK`, `docker-entrypoint.sh` runs `alembic upgrade head` before `uvicorn`); `docker-compose.yml` (app + Postgres, healthcheck-gated); `.github/workflows/ci.yml` (Postgres service container + full test suite) |
| `docs/USER_MANUAL.md` | ✅ created, updated alongside features | — | |
| `docs/DEFINITION_OF_DONE.md` | ✅ done | — | Point-in-time "is v1 shippable" consolidation snapshot |

---

## Master prompt v2 coverage checklist (as of this entry)

Answering directly: **only §1's schema-level groundwork is done. §2, §3, §4, §5, §6, §7, §8 are
NOT implemented yet** — they require `calculator.py`, `llm_parser.py`, `db/models.py`, and
`pdf_generator.py`, none of which exist yet.

| v2 section | What it requires | Status |
|---|---|---|
| §1 Phases & duration | `WorkPhase`, `WorkItem.phase/depends_on/curing_days` in schema; phase graph + `estimated_duration_days` in calculator | Schema: ✅ &nbsp;/&nbsp; Calculator graph: ✅ (topological sort over phases, tested with a screed+curing scenario) |
| §2 Risk buffer + exclusions | Risk formula in calculator; auto-generated exclusions list in PDF | Schema: ✅ &nbsp;/&nbsp; Calculator risk formula: ✅ &nbsp;/&nbsp; Exclusions list: ✅ (generated in calculator, in Polish) &nbsp;/&nbsp; Rendered in PDF: ✅ (`pdf_generator.py`'s "Co NIE wchodzi w zakres kosztorysu" section) |
| §3 Expert-required (heritage) | `EXPERT_REQUIRED` enum value; keyword detection in llm_parser; handoff message + notify-admin trigger | Schema enum: ✅ &nbsp;/&nbsp; Calculator short-circuit (no pricing) + handoff message: ✅ &nbsp;/&nbsp; Keyword detection (llm_parser, bilingual RU/PL, short-circuits before any LLM call): ✅ &nbsp;/&nbsp; admin notify (dialog_manager, calls `MessengerAdapter.send_text` on the configured `admin_channel`/`admin_user_id`): ✅ (tested, `test_heritage_message_sends_handoff_and_notifies_admin`) |
| §4 Design service | `DesignServiceType`, `DesignServiceRequest`, separate pricing, explicit dialogue question | Schema: ✅ &nbsp;/&nbsp; Calculator pricing (3 modes, always separate from construction total): ✅ &nbsp;/&nbsp; llm_parser `DESIGN_SERVICE_QUESTION` constant + `merge_design_service_answer`: ✅ (extraction-time seam only) &nbsp;/&nbsp; actual multi-turn dialogue (`core/dialog_manager.py`'s ask/interpret/retry/carry-forward loop): ✅ (tested) |
| §5 Logistics/seasonal factors | `LogisticsFactor`/`SeasonalFactor` DB tables; applied as itemized labor surcharges | Schema (`AppliedFactor`): ✅ &nbsp;/&nbsp; Calculator application via `PriceRepositoryProtocol`: ✅ (tested) &nbsp;/&nbsp; Real DB tables (`db/models.py`): ✅ (tested against real Postgres) - **note:** `wet_process_allowed` column exists but is not yet consumed by the phase scheduler, see Foreman's Suggestion #4 |
| §6 Three material tiers | `MaterialTier`, per-tier `CostBreakdown` | Schema: ✅ &nbsp;/&nbsp; Calculator producing 3 tiers with shared labor cost: ✅ (tested) &nbsp;/&nbsp; PDF columns: ✅ (`pdf_generator.py`'s three-column `tiers-table`, shared labor row) |
| §7 Contract block in PDF | Payment schedule, warranty terms, exclusions in the rendered PDF | Schema (`ContractorProfile`, `PaymentMilestone`, `WarrantyTerm`): ✅ &nbsp;/&nbsp; Calculator attaches `contractor_profile` to report: ✅ &nbsp;/&nbsp; PDF rendering: ✅ ("Warunki umowy" section) |
| §8 "Voice of the foreman" tone | System prompt style for clarifying questions | ✅ done — `SYSTEM_PROMPT` in `llm_parser.py` (Polish, plain-language, explicitly forbids the LLM from pricing) + `CLARIFYING_QUESTION_BY_FIELD`/`LOW_PRECISION_CLARIFYING_QUESTIONS` avoid jargon like "wastage factor" |

**Conclusion: v1 is feature-complete AND production-hardened.** All 8 steps of the master
prompt's file-generation order are done, plus Dockerfile/docker-compose/CI (all smoke-tested
for real, not just written) and `docs/DEFINITION_OF_DONE.md`. See that file for the full
shippability snapshot. Remaining work (rate limiting, background task queue, structured
logging, a few Foreman's-Suggestion-log ideas) is explicitly deferred and non-blocking - see
`docs/DEFINITION_OF_DONE.md` §3/§5 for the tracked list.


**Open security follow-up (do NOT forget, see Foreman's Suggestion #9):** when
`core/dialog_manager.py` adds the voice/photo pipeline (feeding `InboundMessage.voice_file_url`/
`image_file_url` to an LLM), it MUST download the file bytes server-side first and pass
bytes/base64 to the LLM provider - never forward Telegram's raw file URL (which embeds the
bot token) to a third-party API. This is a concrete, must-fix-before-shipping item, not just
a nice-to-have; flag it again explicitly in that file's own summary when it's built, and treat
it as blocking for the v1 Definition of Done if the Vision/voice path ships without the fix.

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

### 2026-08-12 — messengers/telegram_adapter.py + tests/test_telegram_adapter.py
- Implemented per master prompt v1 section 2/6 (aiogram 3.x; the only channel required for
  the v1 production Definition of Done).
- `receive()` parses Telegram's raw webhook `Update` JSON via plain dict access (no aiogram
  `Update` model needed), handling `message`/`edited_message`; any other update type
  (`callback_query`, `channel_post`, ...) raises `ValueError` since `core/dialog_manager.py`
  (not yet built) is expected to only route message-bearing updates here. Text messages,
  voice messages (`voice`/`audio`), and photos (largest of the `photo` size array, Telegram
  lists smallest-to-largest) are all mapped onto `InboundMessage`.
- Voice/photo `file_id`s are resolved to downloadable URLs via a `getFile` Bot API call
  (`_resolve_file_url`), building `https://api.telegram.org/file/bot<TOKEN>/<file_path>` -
  this is Telegram's own URL scheme, not something invented here.
- The `aiogram.Bot` instance is constructed lazily and injectable via `TelegramAdapter(bot_token,
  bot=...)`, mirroring `llm_parser.py`'s `completion_fn` injection pattern - tests use a
  `FakeBot` (in `tests/test_telegram_adapter.py`) instead of a real bot token/network call.
- **Security note flagged, not yet fixed** (see Foreman's Suggestion #9 below): the resolved
  file URLs embed the bot token. Whatever eventually consumes these (a Vision LLM call in
  `llm_parser.py`/`core/dialog_manager.py`) must not forward the raw URL to a third-party
  provider as-is.
- **Tests: 8/8 passing** in `tests/test_telegram_adapter.py` - constructor rejects an empty
  token, plain text parsing, `edited_message` parsing, voice message + URL resolution, photo
  message picks the largest size + resolves URL, unsupported update type raises, `send_text`
  and `send_document` both call the injected fake bot with the right arguments.
- **Full suite: 85/85 passing** (previous 77 + `tests/test_telegram_adapter.py` 8).
- Added `aiogram>=3` to `requirements.txt`.

### 2026-08-13 — messengers/whatsapp_adapter.py + viber_adapter.py (v1 stubs) + tests
- Implemented per master prompt v1 section 2/6, v1-scope-only stubs (Telegram remains the only
  channel required for the production Definition of Done). `WhatsAppAdapter`/`ViberAdapter` both
  implement `MessengerAdapter` fully (concrete classes, not abstract), validate their required
  credentials in `__init__` (fail fast on empty token/phone_number_id), and raise a clear,
  descriptive `NotImplementedError` from `receive`/`send_text`/`send_document` explaining this
  is a v1 stub and what real implementation would require (WhatsApp Cloud API webhook shape +
  `/messages` endpoint; Viber REST Bot API webhook event shape + `send_message` endpoint).
- Purpose: prove `MessengerChannel`/`MessengerAdapter` is genuinely channel-agnostic (three
  adapters share one contract, not just Telegram), and let `core/dialog_manager.py`/`app.py`
  wire up all three webhook routes today without needing WhatsApp Cloud API/Viber sandbox
  accounts, with real implementations droppable in later without touching any other file.
- **Tests: 11/11 passing** in `tests/test_messenger_stub_adapters.py` - both classes: reject
  empty credentials at construction, expose the correct `channel`, and raise
  `NotImplementedError` from all three interface methods.
- **Full suite: 96/96 passing** (previous 85 + `tests/test_messenger_stub_adapters.py` 11).
- No new dependencies.

### 2026-08-13 — core/dialog_manager.py + tests/test_dialog_manager.py
- Implemented `DialogManager`, the channel-agnostic orchestrator (master prompt section 6,
  step 7 - "точка сборки всего пайплайна"): `adapters: dict[MessengerChannel, MessengerAdapter]`,
  `prices: PriceRepositoryProtocol`, `output_dir`, optional `admin_channel`/`admin_user_id`,
  injectable `completion_fn` (same DI convention as `llm_parser.py`/`telegram_adapter.py`).
- Conversation state machine (`_ConversationState`, keyed by `(channel, user_id)`):
  1. **Text-only gate** - if `message.text is None` (voice/photo only), sends
     `TEXT_ONLY_NOTICE` and returns immediately, without calling `parse_renovation_request` -
     voice/vision transcription is explicitly out of v1 scope (known gap, see module docstring).
  2. **Heritage/`EXPERT_REQUIRED` short-circuit** - `parse_renovation_request` itself detects
     heritage keywords before any LLM call; `_handle_expert_required` sends
     `report.expert_handoff_message` to the user and calls `_notify_admin` (closes v2 §3's
     last open checklist item), then clears the session.
  3. **Design-service question loop (v2 §4)** - `needs_design_service_clarification` fires
     whenever `data.design_service is None`, independent of precision level, so it is asked
     on essentially every fresh conversation before the first PDF. Replies are interpreted by
     `interpret_design_service_reply` (deterministic PL+RU keyword match, mirrors
     `detect_heritage_keywords` - never guesses on a money-adjacent decision); an ambiguous
     reply sends `DESIGN_SERVICE_RETRY_NOTICE` and leaves the session `awaiting_design_service`
     untouched. Once answered, the answer is carried forward across later refinement turns via
     `merge_design_service_answer` (since each turn re-parses `raw_text_history` from scratch
     and would otherwise forget it), so the question is never asked twice in one conversation.
  4. **`_finalize`** - computes the `EstimateReport`, saves the PDF to
     `output_dir/kosztorys_<user_id>_<uuid>.pdf`, sends it via `adapter.send_document`, then
     conditionally sends `report.disclaimer` (MID precision only - LOW/HIGH have none) and a
     `REFINEMENT_INTRO` + joined `clarifying_questions` follow-up (LOW/MID can have up to 5;
     HIGH always has none).
- **Known v1 gap** (flagged in the module docstring and as Foreman's Suggestion #10 below):
  `DEFAULT_DESIGN_FEE_PERCENT = Decimal("0.10")` is a hardcoded stopgap, because
  `calculator.py`'s `_price_design_service` requires a fee value already populated on
  `DesignServiceRequest` and `PriceRepositoryProtocol` has no design-fee getter to look one up.
- **Tests: 12/12 passing** in `tests/test_dialog_manager.py`, reusing `FakePriceRepository`
  from `test_calculator.py` (cross-file fixture reuse convention) and the exact LOW/HIGH JSON
  payload shapes from `test_llm_parser.py`; a local `FakeAdapter` records `sent_texts`/
  `sent_documents` in memory. Covers: voice-only text gate, heritage handoff + admin notify
  (with/without admin configured), design-question-first ordering, ambiguous-reply retry with
  state preservation, full HIGH-precision flow producing a real PDF (`tmp_path`, verifies
  `%PDF` magic bytes), LOW-precision flow's follow-up clarifying-questions text, the
  design-service answer being carried forward across a third refinement turn without
  re-asking, and a clear `ValueError` for an unconfigured channel.
- **Bug found and fixed by these tests**: `interpret_design_service_reply`'s original
  has-vs-needs keyword check order caused "nie mam projektu, proszę wliczyć" to be
  misclassified as "has a design" (`needed=False`), because the loose has-keyword
  `"mam projekt"` is a substring of `"nie mam projektu"`. Fixed by checking the needs-keyword
  list (which includes the negation marker `"nie mam"`) first.
- **Full suite: 108/108 passing** (previous 96 + `tests/test_dialog_manager.py` 12). Confirmed
  the `DYLD_FALLBACK_LIBRARY_PATH` prefix is no longer needed in the test command - WeasyPrint's
  own macOS import-time fix in `pdf_generator.py` is sufficient standalone.
- No new dependencies.

### 2026-08-13 — config.py + app.py + tests/test_app.py (FINAL file, master prompt step 8)
- Implemented `config.py`'s `Settings.from_env()`: reads `TELEGRAM_BOT_TOKEN`/`DATABASE_URL`
  (required, fail-loud `RuntimeError` if missing, same convention as `db/session.py`'s
  `get_database_url()`), optional `OUTPUT_DIR`/`ADMIN_CHANNEL`/`ADMIN_USER_ID`/
  `TELEGRAM_WEBHOOK_SECRET`/WhatsApp/Viber credentials. Validates `ADMIN_CHANNEL` against the
  `MessengerChannel` literal at load time rather than failing later with a confusing error.
  Loads a local `.env` via `python-dotenv` (new dependency) but never at import time - only
  from `app.py`'s `lifespan`, so importing `config.py` never requires env vars to be set.
- Implemented `app.py`'s `create_app(dialog_manager=None, webhook_secret=None)`: with a
  `dialog_manager` given, builds a plain FastAPI app with no lifespan (the test/injection
  path - no DB/network ever touched); with none given, registers an async `lifespan` that
  builds `Settings`, a Postgres engine, loads the `PriceRepository` snapshot, constructs real
  adapters (Telegram always; WhatsApp/Viber only if their credentials are configured) and a
  real `DialogManager`, storing it on `app.state` (the production path - `app = create_app()`
  at module level is what `uvicorn app:app` runs).
- Routes: `GET /healthz`; `POST /webhook/telegram` (verifies
  `X-Telegram-Bot-Api-Secret-Token` against `TELEGRAM_WEBHOOK_SECRET` via
  `secrets.compare_digest` - constant-time comparison, OWASP-relevant since a naive `==`
  string compare leaks timing information about how many leading bytes matched); `POST
  /webhook/whatsapp`/`POST /webhook/viber` (return 501 if the channel isn't configured, or if
  the adapter is still a v1 `NotImplementedError` stub). Any other unexpected exception during
  processing is logged server-side (`logger.exception`) and the route still returns 200 - never
  leaks a stack trace to the caller, and avoids triggering the sender's webhook-retry storm on
  a transient internal error.
- **Tests: 7/7 passing** in `tests/test_app.py`, exclusively via `create_app(dialog_manager=...)`
  injection + `fastapi.testclient.TestClient` - reuses `FakePriceRepository` from
  `test_calculator.py`, a local `FakeAdapter` (parses the raw webhook dict directly into an
  `InboundMessage`, unlike `test_dialog_manager.py`'s `FakeAdapter` whose `receive()` isn't
  used) and a `BoomAdapter` that raises `RuntimeError` from `receive()` to prove the
  "never leak a 500, always return 200" error-handling path. Covers: health check, real
  end-to-end webhook processing (heritage message → handoff text sent), missing/wrong/correct
  webhook secret, unconfigured-channel 501, and the crash-still-returns-200 case.
- **New dependencies**: `fastapi>=0.110`, `uvicorn[standard]>=0.29`, `httpx>=0.27` (only used
  by `fastapi.testclient.TestClient` in tests, not by app.py's production code path),
  `python-dotenv>=1.0`. Added to `requirements.txt`. Created `.env.example` per master prompt
  section 5, plus the extra vars this project actually needs (`DATABASE_URL`, `OUTPUT_DIR`,
  `ADMIN_CHANNEL`, `ADMIN_USER_ID`, `TELEGRAM_WEBHOOK_SECRET`) - `.env` itself was already in
  `.gitignore`.
- **Full suite: 115/115 passing** (previous 108 + `tests/test_app.py` 7).
- **Note**: `fastapi.testclient`'s `TestClient` currently emits a `StarletteDeprecationWarning`
  ("Using httpx with starlette.testclient is deprecated; install httpx2 instead") on this
  Starlette version. Not acting on it now (tests pass, no functional impact; `httpx2` isn't a
  package we recognize well enough to adopt sight-unseen) - revisit when Starlette's own docs
  clarify the migration path.
- All 8 steps of the master prompt's file-generation order are now complete. Remaining work is
  production hardening (Dockerfile, CI, rate limiting, DoD doc) — see Foreman's Suggestion #11.

### 2026-08-13 — Production hardening: Dockerfile, docker-compose.yml, CI, DEFINITION_OF_DONE.md
- **`Dockerfile`**: multi-stage build (`builder` installs deps into a venv, `runtime` copies
  just the venv + source, keeping no compiler/build tools in the final image). Runtime stage
  installs WeasyPrint's native Linux deps (`libpango-1.0-0`, `libpangocairo-1.0-0`,
  `libgdk-pixbuf2.0-0`, `libcairo2`, `shared-mime-info`, `fonts-dejavu-core`) via apt - no
  macOS-style `DYLD_FALLBACK_LIBRARY_PATH` workaround needed on Linux, apt puts them on the
  standard linker path. Runs as a non-root `app` user; `HEALTHCHECK` hits `/healthz`.
  `docker-entrypoint.sh` runs `alembic upgrade head` before `exec`-ing the CMD (`uvicorn
  app:app`), so a container never serves traffic against a stale schema and fails loudly if
  migrations fail.
- **Bug found and fixed while smoke-testing**: the non-root `app` user had no writable
  Fontconfig cache directory, producing a "Fontconfig error: No writable cache directories"
  log line on every startup/PDF render (WeasyPrint → Pango → Fontconfig). Fixed by creating
  `/app/.cache` and setting `XDG_CACHE_HOME=/app/.cache`, owned by `app`.
- **Verified for real, not just "should work"**: ran `docker build`, then
  `docker compose up --build -d` with the real `db` (Postgres 16) + `app` services; confirmed
  `alembic upgrade head` ran automatically on container start, `/healthz` returned 200, and a
  realistic Telegram webhook JSON payload (heritage keyword message) POSTed to
  `/webhook/telegram` was processed end-to-end through the real container (dialog manager →
  calculator → heritage handoff → `TelegramAdapter.send_text` - which correctly raised and was
  caught/logged/200'd since the smoke-test token was a dummy, proving the "never leak a 500"
  contract holds under Docker too). Torn down and cleaned up (`docker compose down -v`, image
  removed, temporary `.env` deleted) after verification.
- **`docker-compose.yml`**: `db` (Postgres 16 alpine, named volume, healthcheck) + `app`
  (builds from the `Dockerfile`, `env_file: .env`, `DATABASE_URL` overridden to point at the
  `db` service by its compose network name, `depends_on: db: condition: service_healthy`,
  named volume for `/app/output`).
- **`.dockerignore`**: excludes `.venv/`, `__pycache__/`, `.git/`, `.env`, `tests/`, `docs/`,
  markdown files, generated `output/` from the build context.
- **`.github/workflows/ci.yml`**: runs on push/PR to `main`; a `postgres:16-alpine` service
  container (port 55432, matching `tests/conftest.py`'s default `TEST_DATABASE_URL`); installs
  the same WeasyPrint apt deps as the Dockerfile; installs `requirements-dev.txt`; runs
  `alembic upgrade head` as a schema sanity check; runs the full test suite.
- **Bug found and fixed while validating the CI workflow locally**: `alembic upgrade head`
  correctly failed against my *reused* local `kosztorys_test_pg` container (its tables were
  already created directly by `tests/conftest.py`'s `Base.metadata.create_all`, without
  Alembic's own `alembic_version` tracking - a `DuplicateTableError`). This is NOT a bug in
  the CI workflow itself - CI's service container starts empty every run. Confirmed the
  workflow is actually correct by spinning up a genuinely fresh, throwaway Postgres container
  (different port) and re-running `alembic upgrade head` + the full suite against it - both
  passed cleanly, matching what CI will see.
- **Second bug found the same way**: my first attempt at validating the CI test step used a
  bare `pytest tests/ -q` and hit `ModuleNotFoundError: No module named 'db'` - bare `pytest`
  does not add the current directory to `sys.path`, only `python -m pytest` does (this project
  relies on that, since `tests/` deliberately has no `__init__.py` for its cross-file fixture
  import convention). Fixed `ci.yml` to use `python -m pytest tests/ -q`. Logged as a general
  gotcha in `/memories/repo/conventions.md` and `/memories/python-testing.md` (user-level, not
  repo-specific, since this trips up any project with a similar test layout).
- **`docs/DEFINITION_OF_DONE.md`** (new file): a point-in-time "is v1 shippable" consolidation
  - scope, file-generation-order status table, production-hardening status table, security
  posture summary, known-gaps summary (cross-referencing the Foreman's Suggestions Log), and
  how-to-run instructions for local/Docker Compose/CI. Distinct from `DIARY.md` (chronological
  engineering log) - this is the "read this one file to know if we can ship" snapshot.
- **Full suite: still 115/115 passing** (no source code changed this phase, only
  infra/docs/CI files - `tests/` unaffected).
- No new Python dependencies (Docker/CI tooling only).

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
9. **[idea, security]** `telegram_adapter.py`'s resolved `voice_file_url`/`image_file_url`
   embed the bot token in the URL path (Telegram's own file-download scheme, not something we
   chose) — this is fine as long as only our own backend fetches these URLs server-side. When
   `core/dialog_manager.py`/`llm_parser.py` add the Vision/voice pipeline, they must download
   the bytes themselves and pass raw bytes/base64 to the LLM provider — never forward the
   Telegram URL as-is to a third-party API (e.g. Gemini Vision's "give me a URL" mode), or the
   bot token leaks to that provider. Flagging now, before that pipeline exists, so it's built
   correctly the first time rather than needing a security-incident retrofit later.
10. **[idea]** `core/dialog_manager.py`'s `DEFAULT_DESIGN_FEE_PERCENT = Decimal("0.10")` (10%
    of budget) is a single hardcoded project-wide fee applied whenever a client says yes to the
    design service question. A real estimating office prices design/documentation services
    differently per contractor, per city, and per project complexity - a 10% flat fee on a
    small bathroom job and a whole-apartment renovation are not comparable asks. Once
    `PriceRepositoryProtocol` gains a design-fee getter (mirroring `get_labor_rate` etc.), this
    constant should be replaced with a real lookup so it's contractor-configurable via the price
    catalog rather than baked into the orchestrator code.
11. **[idea]** `app.py`'s webhook routes have no rate limiting or request body size cap - a
    real deployment gets hit by scanners/bots probing every public endpoint, and each POST to
    `/webhook/telegram` (once past the secret-token check) currently triggers a full
    LLM-call + PDF-generation pipeline. Also: webhook processing currently runs inline in the
    request handler rather than being handed off to a background task/queue - fine for v1
    traffic, but a slow LLM provider response risks missing Telegram's webhook response-time
    expectations under load. Both are standard production-hardening items (rate limiter
    middleware or a reverse-proxy-level limit; a task queue like `arq`/`celery` for the actual
    pipeline work) - flagging now so they aren't forgotten before the real Definition of Done,
    not because anything is broken in the current single-user-testing scope.