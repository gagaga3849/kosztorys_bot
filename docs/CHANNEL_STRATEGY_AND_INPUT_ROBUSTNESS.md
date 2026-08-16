# Channel Strategy & Input-Robustness Requirements

> Companion to [`DEFINITION_OF_DONE.md`](DEFINITION_OF_DONE.md) (is v1 shippable) and
> [`DIARY.md`](DIARY.md) (per-module build log). This document answers a different question:
> **is the product direction right, and what must change for real users to trust it?**
> Written after a live-testing session (2026-08-13) that surfaced real catalog-coverage and
> silent-failure bugs — see `/memories/repo/conventions.md` for the exact bug/fix log.

## 1. Verdict

Do **not** change the core architecture: a channel-agnostic conversation engine
(`core/dialog_manager.py`) driving an LLM extraction step (`llm_parser.py`), a deterministic
pricing engine (`calculator.py` + `price_repository.py`), and a PDF renderer
(`pdf_generator.py`) is the right shape, and `MessengerAdapter` already makes the engine
channel-agnostic by design. What needs to change is **priority and coverage**, not the shape:

1. Re-prioritize a **web front-end** above further Telegram-only polish (§2).
2. Treat **input-quality resilience** as a first-class requirement with concrete acceptance
   criteria, not an LLM-prompting afterthought (§3).
3. Close the **catalog-coverage gap** that caused tonight's live crashes — this is a data/
   engineering problem, not a language-understanding problem (§3.2).

## 2. Channel strategy & phased roadmap

| Phase | Channel | Rationale |
|---|---|---|
| **Phase 1 (build now)** | **Web page**: chat-style UI + structured "here's what we understood" confirmation cards with tap-to-edit chips per field (room, work type, area, material) | Only a rich UI can turn a low-confidence extraction into a fast *confirm-or-correct* interaction instead of "please retype your whole message." This is the highest-leverage fix for the typo/diacritic/grammar problem — see §3.1. Also: fastest iteration loop (no per-channel API constraints), no app-store friction, directly linkable/embeddable. |
| **Phase 1 (keep as-is)** | **Telegram bot** | Already implemented, reuses the same `DialogManager`/`EstimateCalculator`/`pdf_generator` core untouched. Cheap distribution channel for users already on Telegram. `send_choice` (tap-a-button) is the right pattern — extend it to more fields (see §3.1), don't just keep it for the design-service yes/no question. |
| **Phase 2 (demand-driven)** | WhatsApp / Viber | Already wired as stub adapters proving the `MessengerAdapter` contract. Implement for real only once there's evidence (user requests, market data) that these channels matter for the target audience. |
| **Phase 3 / defer indefinitely** | **Native mobile app** | Highest build/maintenance cost, lowest incremental value at this stage: no offline requirement, no sensor/camera need beyond what mobile-web `<input capture>` already provides, and app-store review cycles slow down exactly the iteration speed needed while the LLM prompt and price catalog are still being tuned. Reconsider only after (a) product-market fit is proven via web + bot, and/or (b) a separate B2B "contractor" app becomes a distinct product need (push notifications, job scheduling, offline site visits — a different problem from the consumer estimate flow). |

**Rule of thumb going forward**: any new channel must be a thin adapter over the existing
`DialogManager` core (per `messengers/base.py`'s `MessengerAdapter` contract) — never a
reason to fork business logic. If a channel can't express "confirm/edit a structured field"
(e.g. a plain SMS channel), that channel is lower priority than one that can.

## 3. Input-robustness requirements

### 3.1 Non-negotiable UX requirement: confirm-and-correct, never "please retype"

**Requirement**: after every extraction (regardless of channel), the system MUST show the
user a short summary of what was understood, structured per field, with a fast way to correct
any single field — never a bare "I didn't understand, try again."

- Web: structured cards/chips, each individually editable (dropdown/autocomplete for room and
  work type, numeric stepper for area, etc.) alongside the free-text box.
- Telegram: extend the existing `send_choice` tap-to-select pattern (already used for the
  design-service question) to any field the deterministic layer is unsure about, instead of a
  free-text re-ask.
- An **always-visible "talk to a human" escape hatch** (button/command) must be reachable in
  at most one tap/message at every step of the conversation — this is the actual fix for
  "frustrated user gives up," not perfecting NLP.

**Acceptance test**: for any message that fails to fully parse, the very next bot/web response
must (a) restate what WAS understood, if anything, and (b) offer either tap-to-correct options
or the human-escalation path — an uncaught exception or a bare re-ask is a shipped bug, not an
acceptable edge case.

### 3.2 Catalog-coverage requirement (root cause of tonight's live failures)

**Observation**: tonight's live crashes (`PriceNotFoundError` for `'tiling_bathroom'`,
`'plumbing'`, `'general_renovation'`) were **not** language-understanding failures — the LLM
correctly understood "remont łazienki." They were caused by the LLM being free to invent any
`work_type` identifier while the price catalog only covers a handful of seeded trades
(`scripts/seed_demo_prices.py`). This is a data-modeling problem, solved by:

1. Constrain `work_type` extraction in `llm_parser.py`'s system prompt to a **closed
   enum** matching the real catalog (pass the literal allowed list, or an explicit
   "choose one of [...] or NONE_OF_THESE" instruction) instead of the current free-text
   examples ("np. tiling_floor, demolition_tiling, ...").
2. Add a deterministic fallback classifier (fuzzy or embedding-similarity match of the raw
   phrase against catalog category descriptions) so an item that doesn't map cleanly to a
   known `work_type` is routed to the existing `renovation_generic` fallback rather than
   raising, or is explicitly flagged for human/contractor review.
3. Expand `scripts/seed_demo_prices.py`'s catalog to cover the realistic top-N most common
   renovation trades (bathroom retrofit, painting, electrical points, plumbing rough-in,
   general demolition, flooring beyond tiling) before any real user-facing pilot — a catalog
   this narrow will keep hitting the same class of gap regardless of NLU quality.

**Acceptance test**: every `WorkItem.work_type` the pricing layer receives must resolve to a
priced catalog entry or the generic fallback — `PriceNotFoundError` from a live extraction
should be rare enough to be a genuine catalog gap worth adding, not a routine occurrence.

### 3.3 Typos / missing Polish diacritics / grammar mistakes

- Modern LLM extraction (already in use: Groq `llama-3.3-70b-versatile`) is reasonably
  tolerant of typos and missing diacritics for this kind of extraction task on its own — this
  is *not* expected to be the dominant failure mode once §3.2 is fixed.
- As defense-in-depth (not the primary mechanism), add a deterministic normalization pass
  ahead of/alongside the LLM call for catalog-critical fields (room, material, work type):
  strip diacritics (e.g. `unidecode`) and fuzzy-match (e.g. `rapidfuzz`) against a small
  canonical keyword dictionary, so "lazienka" confidently resolves to "łazienka" even if the
  LLM step alone were to waver.
- Voice input (`voice_transcriber.py`, Groq Whisper) is already implemented and generally
  robust to accents/minor mispronunciation; the same closed-vocabulary/fallback rules from
  §3.2 apply equally to transcribed text — no separate voice-specific NLU work is required.
- **Explicit non-goal**: 100% correct interpretation of arbitrary free text is not an
  achievable or sensible target for any NLP system in this domain. The engineering target is
  graceful degradation (§3.1) and catalog completeness (§3.2), not chasing perfect language
  understanding.

## 4. What "done" looks like for this phase

- [ ] `llm_parser.py`'s system prompt constrains `work_type` to a closed/validated set (or a
      documented fallback path) instead of free-text examples.
- [ ] `scripts/seed_demo_prices.py` (and its production equivalent) covers the realistic top-N
      renovation trades, not just tiling/demolition.
- [ ] A structured "confirm what we understood, edit any field" step exists on at least one
      channel (web, once built) and the Telegram bot's `send_choice` pattern is extended beyond
      the design-service question to any low-confidence field.
- [ ] A "talk to a human" escalation path is reachable in one step from anywhere in the
      conversation, on every channel.
- [ ] A minimal web front-end (Phase 1, §2) exists and reuses `DialogManager` unchanged.

## 5. Non-goals for this phase

- A native mobile app (§2, Phase 3) — explicitly deferred.
- WhatsApp/Viber real implementations (§2, Phase 2) — deferred until demand is evidenced.
- Chasing "perfect" NLU / 100% grammar-and-typo tolerance (§3.3) — replaced by the
  confirm-and-correct UX requirement and catalog-completeness work above.
