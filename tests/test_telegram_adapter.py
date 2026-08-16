"""Tests for `messengers/telegram_adapter.py`.

All tests use a `FakeBot` (in-memory, records calls) injected via `TelegramAdapter(bot_token=
..., bot=...)` - no real Telegram API/network calls, no real bot token needed. This mirrors
`llm_parser.py`'s fake `completion_fn` injection pattern used in `tests/test_llm_parser.py`.
"""

from types import SimpleNamespace

import pytest

from messengers.telegram_adapter import TelegramAdapter


class FakeBot:
    """Records outbound calls; `get_file` returns a fixed `file_path` per `file_id`."""

    def __init__(self) -> None:
        self.sent_messages: list[dict] = []
        self.sent_documents: list[dict] = []
        self.answered_callback_ids: list[str] = []
        self.file_paths_by_id = {
            "voice-1": "voice/file_1.oga",
            "photo-large": "photos/file_2.jpg",
        }

    async def get_file(self, file_id: str):
        return SimpleNamespace(file_path=self.file_paths_by_id[file_id])

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def send_document(self, chat_id, document, caption):
        self.sent_documents.append({"chat_id": chat_id, "document": document, "caption": caption})

    async def answer_callback_query(self, callback_query_id):
        self.answered_callback_ids.append(callback_query_id)

    async def set_my_commands(self, commands):
        self.registered_commands = commands


@pytest.fixture
def fake_bot():
    return FakeBot()


@pytest.fixture
def adapter(fake_bot):
    return TelegramAdapter(bot_token="test-token", bot=fake_bot)


def test_constructor_rejects_empty_bot_token():
    with pytest.raises(ValueError):
        TelegramAdapter(bot_token="")


async def test_receive_parses_plain_text_message(adapter):
    payload = {
        "update_id": 1,
        "message": {"chat": {"id": 12345}, "text": "ремонт ванной 5 кв.м"},
    }
    message = await adapter.receive(payload)

    assert message.channel == "telegram"
    assert message.user_id == "12345"
    assert message.text == "ремонт ванной 5 кв.м"
    assert message.voice_file_url is None
    assert message.image_file_url is None


async def test_receive_parses_edited_message(adapter):
    payload = {
        "update_id": 2,
        "edited_message": {"chat": {"id": 999}, "text": "poprawka"},
    }
    message = await adapter.receive(payload)

    assert message.user_id == "999"
    assert message.text == "poprawka"


async def test_receive_parses_voice_message_and_resolves_url(adapter, fake_bot):
    payload = {
        "message": {
            "chat": {"id": 42},
            "voice": {"file_id": "voice-1"},
        }
    }
    message = await adapter.receive(payload)

    assert message.voice_file_url == "https://api.telegram.org/file/bottest-token/voice/file_1.oga"
    assert message.text is None


async def test_receive_picks_largest_photo_size(adapter, fake_bot):
    payload = {
        "message": {
            "chat": {"id": 42},
            "caption": "kuchnia przed remontem",
            "photo": [
                {"file_id": "photo-small"},
                {"file_id": "photo-large"},
            ],
        }
    }
    message = await adapter.receive(payload)

    assert message.image_file_url == "https://api.telegram.org/file/bottest-token/photos/file_2.jpg"
    assert message.text == "kuchnia przed remontem"


async def test_receive_raises_on_unsupported_update_type(adapter):
    with pytest.raises(ValueError):
        await adapter.receive({"update_id": 3, "channel_post": {"chat": {"id": 1}}})


async def test_receive_parses_callback_query_and_acks_it(adapter, fake_bot):
    payload = {
        "update_id": 4,
        "callback_query": {
            "id": "cbq-1",
            "data": "design_yes",
            "message": {"chat": {"id": 777}},
        },
    }
    message = await adapter.receive(payload)

    assert message.channel == "telegram"
    assert message.user_id == "777"
    assert message.text == "design_yes"
    assert fake_bot.answered_callback_ids == ["cbq-1"]


async def test_send_text_calls_bot_send_message(adapter, fake_bot):
    await adapter.send_text("12345", "Twój kosztorys jest gotowy.")

    assert fake_bot.sent_messages == [
        {"chat_id": "12345", "text": "Twój kosztorys jest gotowy.", "reply_markup": None}
    ]


async def test_send_choice_builds_inline_keyboard_with_callback_data(adapter, fake_bot):
    await adapter.send_choice("12345", "Co dalej?", [("Tak", "design_yes"), ("Nie", "design_no")])

    assert len(fake_bot.sent_messages) == 1
    sent = fake_bot.sent_messages[0]
    assert sent["chat_id"] == "12345"
    assert sent["text"] == "Co dalej?"
    keyboard = sent["reply_markup"].inline_keyboard
    assert [[button.text, button.callback_data] for row in keyboard for button in row] == [
        ["Tak", "design_yes"],
        ["Nie", "design_no"],
    ]


async def test_send_document_calls_bot_send_document(adapter, fake_bot, tmp_path):
    pdf_path = tmp_path / "kosztorys.pdf"
    pdf_path.write_bytes(b"%PDF-fake")

    await adapter.send_document("12345", pdf_path, "Twój kosztorys")

    assert len(fake_bot.sent_documents) == 1
    sent = fake_bot.sent_documents[0]
    assert sent["chat_id"] == "12345"
    assert sent["caption"] == "Twój kosztorys"
    assert str(pdf_path) in str(sent["document"].path)


async def test_ensure_commands_registered_sets_start_command(adapter, fake_bot):
    await adapter.ensure_commands_registered()

    assert len(fake_bot.registered_commands) == 1
    assert fake_bot.registered_commands[0].command == "start"
