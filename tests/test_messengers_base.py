"""Tests for `messengers/base.py` - the channel-agnostic adapter contract.

`MessengerAdapter` is abstract, so these tests exercise it through a minimal in-memory
fake adapter (defined here) that records calls instead of talking to a real platform.
Concrete adapters (`telegram_adapter.py`, etc.) get their own dedicated test files.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from messengers.base import InboundMessage, MessengerAdapter


class FakeAdapter(MessengerAdapter):
    """Minimal concrete adapter used only to prove the abstract contract is usable."""

    channel = "telegram"

    def __init__(self) -> None:
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_documents: list[tuple[str, str, str]] = []

    async def receive(self, raw_payload: dict) -> InboundMessage:
        return InboundMessage(
            channel=self.channel,
            user_id=str(raw_payload["user_id"]),
            text=raw_payload.get("text"),
        )

    async def send_text(self, user_id: str, text: str) -> None:
        self.sent_texts.append((user_id, text))

    async def send_document(self, user_id: str, file_path: str | Path, caption: str) -> None:
        self.sent_documents.append((user_id, str(file_path), caption))


def test_messenger_adapter_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        MessengerAdapter()  # type: ignore[abstract]


async def test_fake_adapter_receive_builds_inbound_message():
    adapter = FakeAdapter()
    message = await adapter.receive({"user_id": 42, "text": "ремонт ванной 5 кв.м"})

    assert message.channel == "telegram"
    assert message.user_id == "42"
    assert message.text == "ремонт ванной 5 кв.м"
    assert message.voice_file_url is None
    assert message.image_file_url is None


async def test_fake_adapter_send_text_records_call():
    adapter = FakeAdapter()
    await adapter.send_text("42", "Twój kosztorys jest gotowy.")

    assert adapter.sent_texts == [("42", "Twój kosztorys jest gotowy.")]


async def test_fake_adapter_send_document_records_call():
    adapter = FakeAdapter()
    await adapter.send_document("42", "/tmp/kosztorys.pdf", "Twój kosztorys")

    assert adapter.sent_documents == [("42", "/tmp/kosztorys.pdf", "Twój kosztorys")]


def test_inbound_message_defaults_are_none_when_only_required_fields_given():
    message = InboundMessage(channel="whatsapp", user_id="user-1")

    assert message.text is None
    assert message.voice_file_url is None
    assert message.image_file_url is None


def test_inbound_message_rejects_unknown_channel():
    with pytest.raises(ValidationError):
        InboundMessage(channel="signal", user_id="user-1")  # type: ignore[arg-type]


def test_inbound_message_accepts_voice_and_image_urls():
    message = InboundMessage(
        channel="viber",
        user_id="user-2",
        voice_file_url="https://example.com/voice.ogg",
        image_file_url="https://example.com/photo.jpg",
    )

    assert message.voice_file_url == "https://example.com/voice.ogg"
    assert message.image_file_url == "https://example.com/photo.jpg"
