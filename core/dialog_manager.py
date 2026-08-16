"""Channel-agnostic conversation orchestrator (master prompt section 6, step 7): the single
place that wires `llm_parser.py` -> `calculator.py` -> `pdf_generator.py` -> a
`MessengerAdapter`, regardless of which channel (Telegram/WhatsApp/Viber) the message came
from. Business logic here never branches on `message.channel` beyond picking the right
adapter to reply through.

Conversation flow per user (keyed by `(channel, user_id)`):
  1. A new text message is appended to that user's running `raw_text` history and re-parsed
     as a whole via `parse_renovation_request` - this lets a client answer one clarifying
     question at a time (e.g. "60x60, straight") without repeating everything they said before.
  2. `EXPERT_REQUIRED` (heritage site) short-circuits immediately: the client gets the
     handoff message, a configured admin gets notified via the same `MessengerAdapter`
     contract (master prompt v2 section 3), and the session is cleared - no PDF, no price.
  3. If the design-service question (master prompt v2 section 4) hasn't been answered yet,
     it is asked ONCE and the session waits for a reply before doing anything else - a
     dedicated deterministic yes/no interpreter (`interpret_design_service_reply`) reads the
     answer, never an LLM guess, keeping money-adjacent decisions deterministic. A
     previously-given answer is carried forward across later refinement turns so the
     question is never asked twice for the same conversation.
  4. Otherwise the estimate is computed (`EstimateCalculator`) and delivered as a PDF
     (`save_estimate_pdf`) + a short text message; if the precision level isn't HIGH yet, the
     remaining clarifying questions are sent as a follow-up so the client can keep refining
     in the same conversation. A tappable "new estimate / end" menu (`send_choice`) follows so
     the client isn't forced to type to continue or stop.
  5. Fixed-choice moments (the design-service question, the post-estimate menu) are always
     sent via `MessengerAdapter.send_choice()` - the client can tap a button instead of typing
     ("never make the client type a fool answer" - master prompt). A reset (`/start`, "reset",
     "zacznij od nowa") or end (`/end`, "end", "zako\u0144cz") trigger is recognized at ANY point
     in the conversation, from a tap or from typing, and short-circuits everything else.
  6. A voice message with no text is transcribed (`voice_transcriber.default_transcribe_fn`)
     and then treated exactly like a typed message - same history/parsing/finalize path. A
     photo-only message still gets a "not supported yet" notice (`TEXT_ONLY_NOTICE`).

Known v1 gaps (flagged, not silently ignored):
  - Photo/vision messages are still acknowledged with a "not supported yet" reply
    (`TEXT_ONLY_NOTICE`) - only voice transcription is implemented so far. See the security
    note in `messengers/telegram_adapter.py` / `voice_transcriber.py`: file bytes are fetched
    server-side (`_default_download_fn`), never forwarded as a raw URL to a third-party
    provider.
  - `DEFAULT_DESIGN_FEE_PERCENT` is a hardcoded business constant, not sourced from
    `PriceRepositoryProtocol` (which has no design-fee getter yet) - flagged as a Foreman's
    Suggestion in docs/DIARY.md for whenever the design-service fee needs to be
    contractor-configurable rather than a single project-wide default.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from calculator import EstimateCalculator, PriceRepositoryProtocol
from llm_parser import (
    DESIGN_SERVICE_QUESTION,
    CompletionFn,
    LLMParsingError,
    default_completion_fn,
    merge_design_service_answer,
    needs_design_service_clarification,
    parse_renovation_request,
)
from messengers.base import ChoiceOptions, InboundMessage, MessengerAdapter, MessengerChannel
from pdf_generator import save_estimate_pdf
from price_repository import PriceNotFoundError
from schema import DesignServiceRequest, DesignServiceType, ExtractedRenovationData, PrecisionLevelEnum
from voice_transcriber import TranscribeFn, default_transcribe_fn

logger = logging.getLogger("kosztorys_bot.dialog_manager")

DownloadFn = Callable[[str], bytes]


def _default_download_fn(url: str) -> bytes:
    """Fetches a messenger-hosted file (e.g. a Telegram voice message) server-side. See the
    security note in `messengers/telegram_adapter.py` / `voice_transcriber.py` - the URL (which
    may embed a bot token) is only ever used here, never forwarded to an LLM/STT provider.
    """
    import httpx  # imported lazily, mirrors the project's DI convention for optional deps

    response = httpx.get(url, timeout=30)
    response.raise_for_status()
    return response.content


# See module docstring's "Known v1 gaps" note - a placeholder until the design-service fee
# is contractor-configurable via PriceRepositoryProtocol.
DEFAULT_DESIGN_FEE_PERCENT = Decimal("0.10")

DESIGN_YES_VALUE = "design_yes"
DESIGN_NO_VALUE = "design_no"
RESET_VALUE = "reset"
END_VALUE = "end"

DESIGN_SERVICE_OPTIONS: ChoiceOptions = (
    ("Tak, potrzebuj\u0119 projektu", DESIGN_YES_VALUE),
    ("Nie, mam ju\u017c projekt", DESIGN_NO_VALUE),
)
POST_ESTIMATE_OPTIONS: ChoiceOptions = (
    ("\U0001f504 Nowa wycena", RESET_VALUE),
    ("\u2705 Zako\u0144cz", END_VALUE),
)
POST_ESTIMATE_MENU_PROMPT = "Co dalej?"

# Recognized at ANY point in the conversation (typed or tapped) - both are checked against
# `text.strip().lower()`, so "/start"/"RESET"/"Zacznij od nowa" all match.
_RESET_TRIGGERS = {RESET_VALUE, "/start", "/reset", "zacznij od nowa"}
_END_TRIGGERS = {END_VALUE, "/end", "/cancel", "zako\u0144cz", "zako\u0144cz rozmow\u0119"}

RESET_CONFIRMATION_TEXT = (
    "Zaczynamy od nowa - opisz, prosz\u0119, zakres prac (mo\u017cesz te\u017c nagra\u0107 "
    "wiadomo\u015b\u0107 g\u0142osow\u0105)."
)
GOODBYE_TEXT = "Dzi\u0119kuj\u0119! Je\u015bli zechcesz now\u0105 wycen\u0119, napisz lub nagraj wiadomo\u015b\u0107 w dowolnym momencie."
VOICE_TRANSCRIPTION_FAILED_NOTICE = (
    "Nie uda\u0142o si\u0119 rozpozna\u0107 wiadomo\u015bci g\u0142osowej - spr\u00f3buj ponownie "
    "albo napisz tekstem."
)

TEXT_ONLY_NOTICE = (
    "Na razie nie obs\u0142uguj\u0119 zdj\u0119\u0107 - opisz, prosz\u0119, zakres prac s\u0142owami "
    "lub wiadomo\u015bci\u0105 g\u0142osow\u0105."
)
DESIGN_SERVICE_RETRY_NOTICE = (
    "Nie jestem pewien odpowiedzi - napisz prosz\u0119 wprost: \u201etak, potrzebuj\u0119"
    " projektu\u201d albo \u201enie, mam ju\u017c projekt\u201d."
)
REFINEMENT_INTRO = "Aby uzyska\u0107 dok\u0142adniejsz\u0105 wycen\u0119, odpowiedz prosz\u0119 na kilka pyta\u0144:"
# Sent instead of crashing silently when the price catalog has no entry for a work type the
# LLM extracted (the demo catalog - see scripts/seed_demo_prices.py - only covers a handful of
# trades; a real deployment's catalog would be far broader). Without this, `_finalize` would
# raise `PriceNotFoundError`, which propagates all the way up to the polling/webhook loop's
# top-level handler and is only logged - the client would never get any reply at all.
PRICE_CATALOG_GAP_NOTICE = (
    "Przepraszamy, nie mamy jeszcze w cenniku wszystkich wskazanych prac, wi\u0119c nie mo\u017cemy"
    " policzy\u0107 pe\u0142nej wyceny. Spr\u00f3buj opisa\u0107 zakres inaczej albo skontaktuj si\u0119"
    " z nami bezpo\u015brednio."
)
# Sent when the LLM fails to return usable structured output (`LLMParsingError` - see
# `llm_parser.py`'s "fail loud rather than guess" comment). Without this, the exception would
# propagate all the way up to the polling/webhook loop's top-level handler and the client
# would never get any reply at all.
LLM_PARSING_FAILED_NOTICE = (
    "Przepraszamy, nie uda\u0142o si\u0119 zrozumie\u0107 wiadomo\u015bci. Spr\u00f3buj, prosz\u0119, opisa\u0107"
    " zakres prac inaczej albo pro\u015bciej."
)

# Deterministic keyword interpretation of the design-service yes/no reply (mirrors
# `llm_parser.detect_heritage_keywords`'s "deterministic substring match, not an LLM guess"
# pattern for anything that affects pricing decisions). PL + RU, matches project convention.
_NEEDS_DESIGN_KEYWORDS: tuple[str, ...] = (
    "nie mam", "potrzebuj\u0119 projektu", "prosz\u0119 wliczy\u0107", "tak, wlicz",
    "\u043d\u0435\u0442 \u043f\u0440\u043e\u0435\u043a\u0442\u0430", "\u043d\u0443\u0436\u0435\u043d \u043f\u0440\u043e\u0435\u043a\u0442",
    "\u0434\u0430, \u0432\u043a\u043b\u044e\u0447",
)
_HAS_DESIGN_KEYWORDS: tuple[str, ...] = (
    "mam ju\u017c projekt", "mam projekt", "nie trzeba", "bez projektu",
    "\u0435\u0441\u0442\u044c \u043f\u0440\u043e\u0435\u043a\u0442", "\u0443\u0436\u0435 \u0435\u0441\u0442\u044c \u043f\u0440\u043e\u0435\u043a\u0442",
    "\u043d\u0435 \u043d\u0443\u0436\u0435\u043d",
)


def interpret_design_service_reply(text: str) -> bool | None:
    """Deterministically interpret a reply to `DESIGN_SERVICE_QUESTION` as True (client
    needs a design service), False (client already has one), or None (unclear - the caller
    should ask again rather than silently guessing, since this feeds a real cost line).
    """
    lowered = text.lower().strip()
    # Exact match against `DESIGN_SERVICE_OPTIONS`' callback values (a tapped button) is
    # checked first - no ambiguity possible there, unlike free-typed text.
    if lowered == DESIGN_YES_VALUE:
        return True
    if lowered == DESIGN_NO_VALUE:
        return False
    # Checked in this order deliberately: "nie mam" (needs) is a substring of phrases like
    # "nie mam projektu" which would otherwise also spuriously match a loose "mam projekt"
    # has-one keyword - the negation marker must win.
    if any(kw in lowered for kw in _NEEDS_DESIGN_KEYWORDS):
        return True
    if any(kw in lowered for kw in _HAS_DESIGN_KEYWORDS):
        return False
    return None


@dataclass
class _ConversationState:
    raw_text_history: list[str] = field(default_factory=list)
    data: ExtractedRenovationData | None = None
    awaiting_design_service: bool = False


class DialogManager:
    """Assembles one full turn of the pipeline for any channel. Stateful (holds an
    in-memory per-conversation session dict) but has no I/O of its own beyond the injected
    `adapters`/`prices`/`completion_fn` - fully testable with fakes for all three.
    """

    def __init__(
        self,
        adapters: dict[MessengerChannel, MessengerAdapter],
        prices: PriceRepositoryProtocol,
        output_dir: str | Path,
        admin_channel: MessengerChannel | None = None,
        admin_user_id: str | None = None,
        completion_fn: CompletionFn = default_completion_fn,
        transcribe_fn: TranscribeFn = default_transcribe_fn,
        download_fn: DownloadFn = _default_download_fn,
    ) -> None:
        self._adapters = adapters
        self._prices = prices
        self._output_dir = Path(output_dir)
        self._admin_channel = admin_channel
        self._admin_user_id = admin_user_id
        self._completion_fn = completion_fn
        self._transcribe_fn = transcribe_fn
        self._download_fn = download_fn
        self._sessions: dict[tuple[str, str], _ConversationState] = {}

    async def handle_webhook(self, channel: MessengerChannel, raw_payload: dict) -> None:
        """Entry point for `app.py`'s webhook routes: translate the raw payload via this
        channel's adapter, then run it through the shared conversation logic."""
        adapter = self._get_adapter(channel)
        message = await adapter.receive(raw_payload)
        await self.handle_message(message)

    async def handle_message(self, message: InboundMessage) -> None:
        adapter = self._get_adapter(message.channel)
        key = (message.channel, message.user_id)
        state = self._sessions.get(key)
        text = message.text

        # Reset/end are recognized at ANY point in the conversation - from a tapped button's
        # `callback_data` or from typing - and always short-circuit everything else below.
        if text is not None:
            normalized = text.strip().lower()
            if normalized in _RESET_TRIGGERS:
                self._sessions.pop(key, None)
                await adapter.send_text(message.user_id, RESET_CONFIRMATION_TEXT)
                return
            if normalized in _END_TRIGGERS:
                self._sessions.pop(key, None)
                await adapter.send_text(message.user_id, GOODBYE_TEXT)
                return

        if text is None and message.voice_file_url:
            text = await self._transcribe_voice(adapter, message.user_id, message.voice_file_url)
            if text is None:
                return  # failure notice already sent to the client

        if text is None:
            if message.image_file_url:
                await adapter.send_text(message.user_id, TEXT_ONLY_NOTICE)
            return

        if state is not None and state.awaiting_design_service:
            await self._handle_design_service_reply(adapter, message.user_id, key, state, text)
            return

        history = list(state.raw_text_history) if state else []
        history.append(text)
        combined_text = "\n".join(history)

        try:
            data = parse_renovation_request(combined_text, completion_fn=self._completion_fn)
        except LLMParsingError:
            # The LLM returned unusable/invalid structured output (master prompt: fail loud
            # rather than guess) - the client must still get a reply, not silence, so they can
            # try rephrasing instead of the conversation appearing to hang.
            logger.exception("LLM parsing failed for user_id=%s", message.user_id)
            await adapter.send_text(message.user_id, LLM_PARSING_FAILED_NOTICE)
            return

        # Carry forward a design-service answer already given earlier in this conversation -
        # `parse_renovation_request` re-extracts from scratch each turn and can't know about it.
        if data.design_service is None and state and state.data and state.data.design_service is not None:
            data = merge_design_service_answer(data, state.data.design_service)

        # The LLM may infer `design_service.needed=True` straight from free text (e.g. the
        # client wrote "tak, potrzebuję projektu" up front) without ever being asked the
        # yes/no question - it never fills in `service_type`/pricing though (that's a business
        # decision, not a fact to extract). Fill in the same default pricing the yes-branch of
        # `_handle_design_service_reply` uses, so the calculator never sees an incomplete
        # request and the client is never re-asked something they already said.
        if data.design_service is not None and data.design_service.needed and data.design_service.service_type is None:
            data = merge_design_service_answer(
                data,
                DesignServiceRequest(
                    needed=True,
                    service_type=DesignServiceType.PERCENT_OF_BUDGET,
                    fee_percent=DEFAULT_DESIGN_FEE_PERCENT,
                ),
            )

        if data.precision_level == PrecisionLevelEnum.EXPERT_REQUIRED:
            await self._handle_expert_required(adapter, message.user_id, data)
            self._sessions.pop(key, None)
            return

        if needs_design_service_clarification(data):
            self._sessions[key] = _ConversationState(
                raw_text_history=history, data=data, awaiting_design_service=True
            )
            await adapter.send_choice(message.user_id, DESIGN_SERVICE_QUESTION, DESIGN_SERVICE_OPTIONS)
            return

        self._sessions[key] = _ConversationState(raw_text_history=history, data=data)
        await self._finalize(adapter, message.user_id, data)

    async def _transcribe_voice(
        self, adapter: MessengerAdapter, user_id: str, voice_file_url: str
    ) -> str | None:
        """Downloads the voice message server-side and transcribes it, so the caller can
        treat the result exactly like a typed message. Returns `None` (having already sent a
        friendly failure notice) if download/transcription fails for any reason - a flaky STT
        provider must never crash the whole webhook turn.
        """
        try:
            audio_bytes = self._download_fn(voice_file_url)
            return self._transcribe_fn(audio_bytes)
        except Exception:
            logger.exception("Voice transcription failed for user_id=%s", user_id)
            await adapter.send_text(user_id, VOICE_TRANSCRIPTION_FAILED_NOTICE)
            return None

    async def _handle_design_service_reply(
        self,
        adapter: MessengerAdapter,
        user_id: str,
        key: tuple[str, str],
        state: _ConversationState,
        reply_text: str,
    ) -> None:
        needs_design = interpret_design_service_reply(reply_text)
        if needs_design is None:
            await adapter.send_choice(user_id, DESIGN_SERVICE_RETRY_NOTICE, DESIGN_SERVICE_OPTIONS)
            return

        design_service = (
            DesignServiceRequest(
                needed=True,
                service_type=DesignServiceType.PERCENT_OF_BUDGET,
                fee_percent=DEFAULT_DESIGN_FEE_PERCENT,
            )
            if needs_design
            else DesignServiceRequest(needed=False)
        )
        assert state.data is not None  # awaiting_design_service is only ever set alongside data
        data = merge_design_service_answer(state.data, design_service)
        self._sessions[key] = _ConversationState(raw_text_history=state.raw_text_history, data=data)
        await self._finalize(adapter, user_id, data)

    async def _handle_expert_required(
        self, adapter: MessengerAdapter, user_id: str, data: ExtractedRenovationData
    ) -> None:
        report = EstimateCalculator(data, self._prices).calculate()
        await adapter.send_text(user_id, report.expert_handoff_message or "")
        await self._notify_admin(
            f"Zg\u0142oszenie wymaga eksperta (heritage): user_id={user_id}, "
            f"keywords={data.heritage_keywords_matched}"
        )

    async def _notify_admin(self, text: str) -> None:
        if self._admin_channel is None or self._admin_user_id is None:
            return
        admin_adapter = self._get_adapter(self._admin_channel)
        await admin_adapter.send_text(self._admin_user_id, text)

    async def _finalize(
        self, adapter: MessengerAdapter, user_id: str, data: ExtractedRenovationData
    ) -> None:
        try:
            report = EstimateCalculator(data, self._prices).calculate()
        except PriceNotFoundError:
            # A catalog gap (e.g. a work type the LLM extracted has no seeded price) must
            # never leave the client without any reply at all - see PRICE_CATALOG_GAP_NOTICE.
            logger.exception("Price catalog gap while finalizing estimate for user_id=%s", user_id)
            await adapter.send_text(user_id, PRICE_CATALOG_GAP_NOTICE)
            return
        pdf_path = save_estimate_pdf(
            report, data, self._output_dir / f"kosztorys_{user_id}_{uuid.uuid4().hex}.pdf"
        )
        await adapter.send_document(user_id, pdf_path, "Tw\u00f3j kosztorys")

        if report.disclaimer:
            await adapter.send_text(user_id, report.disclaimer)

        if report.clarifying_questions:
            questions = "\n".join(f"- {q}" for q in report.clarifying_questions)
            await adapter.send_text(user_id, f"{REFINEMENT_INTRO}\n{questions}")

        await adapter.send_choice(user_id, POST_ESTIMATE_MENU_PROMPT, POST_ESTIMATE_OPTIONS)

    def _get_adapter(self, channel: MessengerChannel) -> MessengerAdapter:
        try:
            return self._adapters[channel]
        except KeyError as exc:
            raise ValueError(f"No adapter configured for channel={channel!r}") from exc
