"""Tests for `core/dialog_manager.py` - the full pipeline assembly point.

Uses: `FakePriceRepository`/`_high_precision_data` imported from `test_calculator` (reuse,
not duplication - see repo convention), a local `FakeAdapter` (in-memory `MessengerAdapter`),
and small hand-rolled fake `completion_fn`s (mirroring `test_llm_parser.py`'s pattern) so no
real LLM call, real Telegram API call, or real WeasyPrint dependency surprise ever happens.
"""

from __future__ import annotations

import json

import pytest

from core.dialog_manager import (
    DESIGN_NO_VALUE,
    DESIGN_SERVICE_OPTIONS,
    DESIGN_SERVICE_RETRY_NOTICE,
    GOODBYE_TEXT,
    LLM_PARSING_FAILED_NOTICE,
    POST_ESTIMATE_MENU_PROMPT,
    POST_ESTIMATE_OPTIONS,
    RESET_CONFIRMATION_TEXT,
    RESET_VALUE,
    TEXT_ONLY_NOTICE,
    VOICE_TRANSCRIPTION_FAILED_NOTICE,
    DialogManager,
    interpret_design_service_reply,
)
from llm_parser import DESIGN_SERVICE_QUESTION
from messengers.base import InboundMessage, MessengerAdapter, MessengerChannel
from price_repository import PriceNotFoundError
from test_calculator import FakePriceRepository


class FakeAdapter(MessengerAdapter):
    def __init__(self, channel: MessengerChannel = "telegram") -> None:
        self.channel = channel
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_documents: list[tuple[str, str, str]] = []
        self.sent_choices: list[tuple[str, str, list]] = []

    async def receive(self, raw_payload: dict) -> InboundMessage:
        raise NotImplementedError("not used in these tests - InboundMessage is built directly")

    async def send_text(self, user_id: str, text: str) -> None:
        self.sent_texts.append((user_id, text))

    async def send_document(self, user_id: str, file_path, caption: str) -> None:
        self.sent_documents.append((user_id, str(file_path), caption))

    async def send_choice(self, user_id: str, text: str, options) -> None:
        self.sent_choices.append((user_id, text, list(options)))


def _fake_completion(response_json: dict):
    def _fn(system_prompt: str, user_prompt: str) -> str:
        return json.dumps(response_json)

    return _fn


def _fake_completion_sequence(responses: list):
    calls = {"n": 0}

    def _fn(system_prompt: str, user_prompt: str) -> str:
        response = responses[calls["n"]]
        calls["n"] += 1
        return json.dumps(response)

    return _fn


def _explode_if_called(system_prompt: str, user_prompt: str) -> str:
    raise AssertionError("completion_fn should not be called for this message")


LOW_PRECISION_RESPONSE = {
    "rooms": ["bathroom"],
    "total_area_m2": 5.0,
    "work_items": [{"work_type": "bathroom_renovation_generic", "quantity": 5.0, "unit": "m2"}],
}

HIGH_PRECISION_RESPONSE = {
    "rooms": ["bathroom"],
    "total_area_m2": 5.0,
    "is_old_building": True,
    "work_items": [
        {
            "work_type": "demolition_tiling",
            "quantity": 17.0,
            "unit": "m2",
            "substrate_condition": "poor",
            "phase": "demolition",
        },
        {
            "work_type": "tiling_floor",
            "quantity": 5.0,
            "unit": "m2",
            "material": "ceramic tile",
            "tile_size_cm": "120x60",
            "layout_pattern": "diagonal",
            "phase": "finish",
            "depends_on": ["demolition"],
        },
    ],
}


@pytest.fixture
def prices():
    return FakePriceRepository()


@pytest.fixture
def adapter():
    return FakeAdapter()


def _manager(adapter, prices, tmp_path, completion_fn, **kwargs) -> DialogManager:
    return DialogManager(
        adapters={"telegram": adapter},
        prices=prices,
        output_dir=tmp_path,
        completion_fn=completion_fn,
        **kwargs,
    )


# --------------------------------------------------------------------------------------
# interpret_design_service_reply
# --------------------------------------------------------------------------------------


def test_interpret_design_service_reply_detects_needed():
    assert interpret_design_service_reply("Nie mam projektu, prosz\u0119 wliczy\u0107") is True


def test_interpret_design_service_reply_detects_has_one():
    assert interpret_design_service_reply("Mam ju\u017c projekt wn\u0119trza") is False


def test_interpret_design_service_reply_unclear_returns_none():
    assert interpret_design_service_reply("nie wiem jeszcze") is None


# --------------------------------------------------------------------------------------
# Voice messages (transcribed) / photo-only messages (still a known v1 gap)
# --------------------------------------------------------------------------------------


def _fake_transcribe(text: str):
    def _fn(audio_bytes: bytes) -> str:
        return text

    return _fn


def _fake_download(content: bytes = b"fake-audio-bytes"):
    def _fn(url: str) -> bytes:
        return content

    return _fn


async def test_voice_message_is_transcribed_and_flows_like_typed_text(adapter, prices, tmp_path):
    manager = _manager(
        adapter,
        prices,
        tmp_path,
        completion_fn=_fake_completion(HIGH_PRECISION_RESPONSE),
        transcribe_fn=_fake_transcribe("remont \u0142azienki"),
        download_fn=_fake_download(),
    )
    message = InboundMessage(channel="telegram", user_id="u1", voice_file_url="https://example.com/v.oga")

    await manager.handle_message(message)

    # The transcribed text went through the exact same pipeline as a typed message - the
    # design-service question follows, via tappable buttons.
    assert adapter.sent_choices[0][1] == DESIGN_SERVICE_QUESTION


async def test_voice_transcription_failure_sends_friendly_notice_not_crash(adapter, prices, tmp_path):
    def _boom(audio_bytes: bytes) -> str:
        raise RuntimeError("STT provider unavailable")

    manager = _manager(
        adapter,
        prices,
        tmp_path,
        completion_fn=_explode_if_called,
        transcribe_fn=_boom,
        download_fn=_fake_download(),
    )
    message = InboundMessage(channel="telegram", user_id="u1", voice_file_url="https://example.com/v.oga")

    await manager.handle_message(message)

    assert adapter.sent_texts == [("u1", VOICE_TRANSCRIPTION_FAILED_NOTICE)]


async def test_photo_only_message_sends_text_only_notice_without_calling_llm(adapter, prices, tmp_path):
    manager = _manager(adapter, prices, tmp_path, completion_fn=_explode_if_called)
    message = InboundMessage(channel="telegram", user_id="u1", image_file_url="https://example.com/p.jpg")

    await manager.handle_message(message)

    assert adapter.sent_texts == [("u1", TEXT_ONLY_NOTICE)]


# --------------------------------------------------------------------------------------
# Reset / end triggers (buttons or typed) - recognized at any point in the conversation
# --------------------------------------------------------------------------------------


async def test_reset_trigger_clears_session_and_confirms(adapter, prices, tmp_path):
    manager = _manager(adapter, prices, tmp_path, completion_fn=_fake_completion(HIGH_PRECISION_RESPONSE))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="remont \u0142azienki"))
    assert ("telegram", "u1") in manager._sessions

    # RESET_VALUE is exactly what a tapped "Nowa wycena" button sends back as `text`.
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text=RESET_VALUE))

    assert ("telegram", "u1") not in manager._sessions
    assert adapter.sent_texts[-1] == ("u1", RESET_CONFIRMATION_TEXT)


async def test_end_trigger_via_typed_command_clears_session_and_says_goodbye(adapter, prices, tmp_path):
    manager = _manager(adapter, prices, tmp_path, completion_fn=_fake_completion(HIGH_PRECISION_RESPONSE))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="remont \u0142azienki"))

    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="/end"))

    assert ("telegram", "u1") not in manager._sessions
    assert adapter.sent_texts[-1] == ("u1", GOODBYE_TEXT)


# --------------------------------------------------------------------------------------
# Heritage / EXPERT_REQUIRED short-circuit (master prompt v2 section 3)
# --------------------------------------------------------------------------------------


async def test_heritage_message_sends_handoff_and_notifies_admin(adapter, prices, tmp_path):
    manager = _manager(
        adapter, prices, tmp_path,
        completion_fn=_explode_if_called,  # heritage short-circuits before any LLM call
        admin_channel="telegram", admin_user_id="admin-1",
    )
    message = InboundMessage(channel="telegram", user_id="u1", text="to jest budynek zabytkowy, chron\u0119ty")

    await manager.handle_message(message)

    assert len(adapter.sent_texts) == 2
    user_reply_user_id, user_reply_text = adapter.sent_texts[0]
    admin_user_id, admin_text = adapter.sent_texts[1]
    assert user_reply_user_id == "u1"
    assert "konserwatorem" in user_reply_text
    assert admin_user_id == "admin-1"
    assert "u1" in admin_text
    assert adapter.sent_documents == []
    assert ("telegram", "u1") not in manager._sessions


async def test_heritage_message_without_admin_configured_does_not_crash(adapter, prices, tmp_path):
    manager = _manager(adapter, prices, tmp_path, completion_fn=_explode_if_called)
    message = InboundMessage(channel="telegram", user_id="u1", text="budynek zabytkowy")

    await manager.handle_message(message)

    assert len(adapter.sent_texts) == 1  # only the user reply, no admin configured


# --------------------------------------------------------------------------------------
# Design-service question flow (master prompt v2 section 4)
# --------------------------------------------------------------------------------------


async def test_design_service_question_asked_before_any_estimate(adapter, prices, tmp_path):
    manager = _manager(adapter, prices, tmp_path, completion_fn=_fake_completion(HIGH_PRECISION_RESPONSE))
    message = InboundMessage(channel="telegram", user_id="u1", text="remont \u0142azienki")

    await manager.handle_message(message)

    assert adapter.sent_choices == [("u1", DESIGN_SERVICE_QUESTION, list(DESIGN_SERVICE_OPTIONS))]
    assert adapter.sent_documents == []


async def test_design_service_answered_via_tapped_button_value(adapter, prices, tmp_path):
    manager = _manager(adapter, prices, tmp_path, completion_fn=_fake_completion(HIGH_PRECISION_RESPONSE))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="remont \u0142azienki"))

    # DESIGN_NO_VALUE is exactly what a tapped "Nie, mam ju\u017c projekt" button sends back.
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text=DESIGN_NO_VALUE))

    assert len(adapter.sent_documents) == 1


async def test_design_service_unclear_reply_asks_again_and_preserves_state(adapter, prices, tmp_path):
    manager = _manager(adapter, prices, tmp_path, completion_fn=_fake_completion(HIGH_PRECISION_RESPONSE))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="remont \u0142azienki"))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="nie wiem jeszcze"))

    assert adapter.sent_choices[-1] == ("u1", DESIGN_SERVICE_RETRY_NOTICE, list(DESIGN_SERVICE_OPTIONS))
    assert manager._sessions[("telegram", "u1")].awaiting_design_service is True


async def test_finalize_sends_post_estimate_choice_menu(adapter, prices, tmp_path):
    manager = _manager(adapter, prices, tmp_path, completion_fn=_fake_completion(HIGH_PRECISION_RESPONSE))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="remont \u0142azienki"))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="mam ju\u017c projekt"))

    assert adapter.sent_choices[-1] == ("u1", POST_ESTIMATE_MENU_PROMPT, list(POST_ESTIMATE_OPTIONS))


async def test_full_conversation_high_precision_delivers_pdf_after_design_answer(adapter, prices, tmp_path):
    manager = _manager(adapter, prices, tmp_path, completion_fn=_fake_completion(HIGH_PRECISION_RESPONSE))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="remont \u0142azienki"))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="mam ju\u017c projekt"))

    assert len(adapter.sent_documents) == 1
    doc_user_id, doc_path, doc_caption = adapter.sent_documents[0]
    assert doc_user_id == "u1"
    assert doc_caption == "Tw\u00f3j kosztorys"
    with open(doc_path, "rb") as f:
        assert f.read(4) == b"%PDF"
    # HIGH precision -> no disclaimer, no remaining clarifying questions
    assert not any("standardowych za\u0142o\u017ce\u0144" in t for _, t in adapter.sent_texts)


async def test_low_precision_flow_sends_pdf_and_followup_clarifying_questions(adapter, prices, tmp_path):
    manager = _manager(adapter, prices, tmp_path, completion_fn=_fake_completion(LOW_PRECISION_RESPONSE))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="remont \u0142azienki 5 m2"))
    await manager.handle_message(
        InboundMessage(channel="telegram", user_id="u1", text="nie mam projektu, prosz\u0119 wliczy\u0107")
    )

    assert len(adapter.sent_documents) == 1
    followup_texts = [t for _, t in adapter.sent_texts]
    assert any("Aby uzyska\u0107 dok\u0142adniejsz\u0105 wycen\u0119" in t for t in followup_texts)


async def test_finalize_sends_friendly_notice_instead_of_crashing_on_unpriced_work_type(
    adapter, tmp_path
):
    """A work_type the LLM extracted but that has no seeded labor rate (a real catalog gap -
    mirrors the real, fail-loud `price_repository.PriceRepository.get_labor_rate`, unlike
    `FakePriceRepository`'s forgiving default) must still produce a reply, not silence - see
    PRICE_CATALOG_GAP_NOTICE."""

    class RaisingPriceRepository(FakePriceRepository):
        def get_labor_rate(self, work_type: str):
            try:
                return self.labor_rates[work_type]
            except KeyError as exc:
                raise PriceNotFoundError(f"No labor rate configured for work_type={work_type!r}") from exc

    unpriced_response = {
        "rooms": ["bathroom"],
        "total_area_m2": 5.0,
        "work_items": [{"work_type": "tiling_bathroom", "quantity": 5.0, "unit": "m2"}],
    }
    manager = _manager(
        adapter, RaisingPriceRepository(), tmp_path, completion_fn=_fake_completion(unpriced_response)
    )
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="remont \u0142azienki"))
    await manager.handle_message(
        InboundMessage(channel="telegram", user_id="u1", text="nie mam projektu, prosz\u0119 wliczy\u0107")
    )

    assert adapter.sent_documents == []
    assert any("nie mo\u017cemy" in t or "cenniku" in t for _, t in adapter.sent_texts)


async def test_llm_parsing_failure_sends_friendly_notice_instead_of_crashing(adapter, prices, tmp_path):
    """A work_item missing `work_type` (LLM returned unusable structured output) raises
    `LLMParsingError` from `parse_renovation_request` - the client must still get a reply, not
    silence, see LLM_PARSING_FAILED_NOTICE."""
    invalid_response = {
        "rooms": ["bathroom"],
        "total_area_m2": 5.0,
        "work_items": [{"work_type": None, "phase": None}],
    }
    manager = _manager(adapter, prices, tmp_path, completion_fn=_fake_completion(invalid_response))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="remont \u0142azienki"))

    assert adapter.sent_documents == []
    assert adapter.sent_texts == [("u1", LLM_PARSING_FAILED_NOTICE)]


async def test_design_service_answer_carried_forward_across_refinement_turn(adapter, prices, tmp_path):
    completion_fn = _fake_completion_sequence([LOW_PRECISION_RESPONSE, HIGH_PRECISION_RESPONSE])
    manager = _manager(adapter, prices, tmp_path, completion_fn=completion_fn)

    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="remont \u0142azienki 5 m2"))
    await manager.handle_message(InboundMessage(channel="telegram", user_id="u1", text="mam ju\u017c projekt"))
    assert len(adapter.sent_documents) == 1

    # Third message adds detail, LLM now returns HIGH-precision fields - design service
    # question must NOT be asked again.
    texts_before_third_turn = len(adapter.sent_texts)
    await manager.handle_message(
        InboundMessage(channel="telegram", user_id="u1", text="p\u0142ytki 120x60, uk\u0142ad po przek\u0105tnej")
    )

    assert len(adapter.sent_documents) == 2
    new_texts = [t for _, t in adapter.sent_texts[texts_before_third_turn:]]
    assert DESIGN_SERVICE_QUESTION not in new_texts


async def test_unconfigured_channel_raises_clear_error(prices, tmp_path):
    manager = DialogManager(adapters={}, prices=prices, output_dir=tmp_path, completion_fn=_explode_if_called)
    message = InboundMessage(channel="telegram", user_id="u1", text="hello")

    with pytest.raises(ValueError):
        await manager.handle_message(message)
