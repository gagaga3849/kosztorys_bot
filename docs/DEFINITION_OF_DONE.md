# Definition of Done — Kosztorys Bot (v1)

> Consolidation snapshot. Source of truth for day-to-day engineering decisions remains
> [`DIARY.md`](DIARY.md) (updated per-file, chronological) — this document is a point-in-time
> "is v1 shippable" summary, refreshed whenever the answer changes materially.

## 1. Scope of "v1"

Per `kosztorys_bot_master_prompt.md` + `kosztorys_bot_master_prompt_v2.md`: a Telegram bot
(the only channel required for v1) that turns a free-text renovation request into a priced,
PDF `Kosztorys Budowlany` at one of three precision levels (LOW/MID/HIGH), with a hard
handoff-to-human path for heritage-protected sites and an optional interior-design-service
line item. WhatsApp/Viber are wired as channel-agnostic stubs (contract proven, not
implemented) — explicitly out of v1 scope.

## 2. File-generation order — all 8 steps complete

| # | File | Status |
|---|---|---|
| 1 | `schema.py` | ✅ done |
| 2 | `calculator.py` + tests | ✅ done, 20/20 |
| 3 | `db/models.py` + `price_repository.py` + Alembic | ✅ done, 16/16 |
| 4 | `llm_parser.py` + tests | ✅ done, 24/24 |
| 5 | `pdf_generator.py` + tests | ✅ done, 10/10 |
| 6 | `messengers/base.py` + 3 adapters + tests | ✅ done, 7+8+11 |
| 7 | `core/dialog_manager.py` + tests | ✅ done, 12/12 |
| 8 | `config.py` + `app.py` + tests | ✅ done, 7/7 |

**Full test suite: 115/115 passing.** Run via:
```
docker start kosztorys_test_pg >/dev/null 2>&1
source .venv/bin/activate
TEST_DATABASE_URL="postgresql+asyncpg://postgres:test@localhost:55432/kosztorys_test" \
  python -m pytest tests/ -q
```
(Always `python -m pytest`, never bare `pytest` — see `/memories/repo/conventions.md`'s
sys.path gotcha.) Tests requiring Postgres skip gracefully if no test DB is reachable.

## 3. Production hardening — this phase

| Item | Status |
|---|---|
| `Dockerfile` (multi-stage, non-root user, WeasyPrint native libs, healthcheck) | ✅ done, build+run smoke-tested locally (image built, container started, `/healthz` 200, a real heritage-message webhook payload processed end-to-end through `alembic upgrade head` → FastAPI → `DialogManager` → `EstimateCalculator` → `TelegramAdapter`) |
| `docker-compose.yml` (app + Postgres, healthcheck-gated startup) | ✅ done, smoke-tested (`docker compose up` → both containers healthy) |
| `.dockerignore` | ✅ done |
| GitHub Actions CI (`.github/workflows/ci.yml`) | ✅ done — Postgres service container, WeasyPrint apt deps, `alembic upgrade head` sanity check, full test suite. Locally validated against a **fresh** Postgres container (not the reused local dev DB) to catch the exact conditions CI will see |
| `.env.example` | ✅ done (created alongside `app.py`) |
| Rate limiting / request size caps on webhook routes | ⬜ not done — flagged as Foreman's Suggestion #11 |
| Background task queue for the LLM+PDF pipeline (vs. inline request handling) | ⬜ not done — flagged as Foreman's Suggestion #11 |
| Structured/JSON logging, log aggregation | ⬜ not done |
| Secrets management beyond `.env`/container env vars (e.g. a real secrets manager) | ⬜ not done — acceptable for v1 given container-level env injection, revisit before scaling |

## 4. Security posture (OWASP-relevant items applied)

- Telegram webhook verifies `X-Telegram-Bot-Api-Secret-Token` via `secrets.compare_digest`
  (constant-time) against `TELEGRAM_WEBHOOK_SECRET` when configured.
- Config (`config.py`) fails loudly (`RuntimeError`) on missing required secrets rather than
  silently defaulting — no accidental "works with an empty token" footgun.
- `.env` is gitignored; only `.env.example` (no real secrets) is committed.
- Webhook processing errors are logged server-side only — never reflected into the HTTP
  response body (no stack-trace leakage).
- Missing pricing data (`price_repository.py`) raises loudly (`PriceNotFoundError`) rather
  than silently guessing a number — a money-safety control, not just a correctness one.
- **Open, tracked, not yet fixed**: when the voice/photo pipeline is eventually built,
  Telegram's resolved file URLs (which embed the bot token) must be fetched server-side and
  never forwarded as-is to a third-party LLM provider. See `DIARY.md` Foreman's Suggestion #9
  — this is a hard blocker for that specific future feature, not for v1's current text-only
  scope.

## 5. Known v1 gaps (intentional, tracked — not blocking the text-only Telegram DoD)

See `DIARY.md`'s full Foreman's Suggestions Log for details on all of these:
1. Voice/photo messages get a "text only for now" reply — no transcription/vision pipeline yet.
2. `DEFAULT_DESIGN_FEE_PERCENT` (10%) is a hardcoded stopgap, not sourced from the price
   catalog (Suggestion #10).
3. Load-bearing wall changes aren't auto-routed to `EXPERT_REQUIRED` like heritage sites yet
   (Suggestion #2).
4. `SeasonalFactor.wet_process_allowed` affects pricing but doesn't yet shift the phase
   schedule (Suggestion #4).
5. No rate limiting/background task queue on webhook routes (Suggestion #11).
6. Contract PDF has no signature/NIP-REGON block yet (Suggestion #8).

## 6. How to run

- **Local dev (no Docker)**: `source .venv/bin/activate`, set env vars per `.env.example`
  (or `cp .env.example .env` and fill in), `alembic upgrade head`, then
  `uvicorn app:app --reload`.
- **Docker Compose** (app + Postgres in one command): `cp .env.example .env` (fill in
  `TELEGRAM_BOT_TOKEN` at minimum), `docker compose up --build`. App listens on `:8000`,
  Postgres on `:5432`.
- **CI**: any push/PR to `main` runs the full test suite against a fresh Postgres service
  container via `.github/workflows/ci.yml`.

## 7. Verdict

**v1's Telegram-only, text-only production Definition of Done is met**: every file in the
master prompt's generation order exists, is tested (115/115), and is containerized/CI'd.
Remaining items (§3/§5 above) are explicitly deferred, tracked, and non-blocking for shipping
the current scope.
