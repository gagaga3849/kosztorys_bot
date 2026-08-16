"""Tests for `app.py` - the FastAPI webhook assembly.

Uses `create_app(dialog_manager=...)`'s injection path exclusively, so no real Postgres, no
real Telegram/WhatsApp/Viber API, and no `.env`/env vars are ever needed here. Reuses
`FakePriceRepository` from `test_calculator.py` (cross-file fixture reuse convention) and
builds a real `core.dialog_manager.DialogManager` around a small local in-memory `FakeAdapter`
so these tests exercise the actual webhook -> dialog manager -> adapter wiring, not a mock of
it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import create_app
from core.dialog_manager import DialogManager
from messengers.base import InboundMessage, MessengerAdapter, MessengerChannel
from test_calculator import FakePriceRepository


class FakeAdapter(MessengerAdapter):
    def __init__(self, channel: MessengerChannel = "telegram") -> None:
        self.channel = channel
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_documents: list[tuple[str, str, str]] = []

    async def receive(self, raw_payload: dict) -> InboundMessage:
        return InboundMessage(channel=self.channel, user_id=raw_payload["user_id"], text=raw_payload.get("text"))

    async def send_text(self, user_id: str, text: str) -> None:
        self.sent_texts.append((user_id, text))

    async def send_document(self, user_id: str, file_path, caption: str) -> None:
        self.sent_documents.append((user_id, str(file_path), caption))


class BoomAdapter(MessengerAdapter):
    """Simulates an unexpected crash while processing a webhook, to prove the route never
    leaks a 500/stack trace to the caller."""

    channel: MessengerChannel = "telegram"

    async def receive(self, raw_payload: dict) -> InboundMessage:
        raise RuntimeError("boom")

    async def send_text(self, user_id: str, text: str) -> None:
        raise NotImplementedError

    async def send_document(self, user_id: str, file_path, caption: str) -> None:
        raise NotImplementedError


def _explode_if_called(system_prompt: str, user_prompt: str) -> str:
    raise AssertionError("completion_fn should not be called for this message")


def _dialog_manager(adapter, tmp_path, **kwargs) -> DialogManager:
    return DialogManager(
        adapters={adapter.channel: adapter},
        prices=FakePriceRepository(),
        output_dir=tmp_path,
        completion_fn=_explode_if_called,
        **kwargs,
    )


def test_healthz_returns_ok(tmp_path):
    app = create_app(dialog_manager=_dialog_manager(FakeAdapter(), tmp_path))
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_telegram_webhook_processes_message_via_dialog_manager(tmp_path):
    adapter = FakeAdapter()
    app = create_app(dialog_manager=_dialog_manager(adapter, tmp_path))
    client = TestClient(app)

    response = client.post("/webhook/telegram", json={"user_id": "u1", "text": "budynek zabytkowy"})

    assert response.status_code == 200
    assert len(adapter.sent_texts) == 1
    assert adapter.sent_texts[0][0] == "u1"


def test_telegram_webhook_rejects_missing_secret_when_configured(tmp_path):
    adapter = FakeAdapter()
    app = create_app(dialog_manager=_dialog_manager(adapter, tmp_path), webhook_secret="s3cr3t")
    client = TestClient(app)

    response = client.post("/webhook/telegram", json={"user_id": "u1", "text": "hi"})

    assert response.status_code == 403
    assert adapter.sent_texts == []


def test_telegram_webhook_rejects_wrong_secret(tmp_path):
    adapter = FakeAdapter()
    app = create_app(dialog_manager=_dialog_manager(adapter, tmp_path), webhook_secret="s3cr3t")
    client = TestClient(app)

    response = client.post(
        "/webhook/telegram",
        json={"user_id": "u1", "text": "hi"},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )

    assert response.status_code == 403
    assert adapter.sent_texts == []


def test_telegram_webhook_accepts_correct_secret(tmp_path):
    adapter = FakeAdapter()
    app = create_app(dialog_manager=_dialog_manager(adapter, tmp_path), webhook_secret="s3cr3t")
    client = TestClient(app)

    response = client.post(
        "/webhook/telegram",
        json={"user_id": "u1", "text": "budynek zabytkowy"},
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
    )

    assert response.status_code == 200
    assert len(adapter.sent_texts) == 1


def test_whatsapp_webhook_returns_501_when_not_configured(tmp_path):
    # Only "telegram" is registered in the adapters dict - whatsapp is absent.
    app = create_app(dialog_manager=_dialog_manager(FakeAdapter(), tmp_path))
    client = TestClient(app)

    response = client.post("/webhook/whatsapp", json={"user_id": "u1", "text": "hi"})

    assert response.status_code == 501


def test_unhandled_processing_error_still_returns_200(tmp_path):
    app = create_app(dialog_manager=_dialog_manager(BoomAdapter(), tmp_path))
    client = TestClient(app)

    response = client.post("/webhook/telegram", json={"user_id": "u1", "text": "hi"})

    assert response.status_code == 200
