"""Telegram channel adapter (master prompt v1 section 2, section 6 step 6) — the only
channel required for the v1 production Definition of Done. Built on `aiogram` 3.x.

Design notes:
  - `receive()` parses Telegram's raw webhook `Update` JSON using plain dict access, not
    aiogram's own `Update` model - this keeps the "did we read the payload correctly"
    logic testable with a bare dict and no aiogram object construction. The one part of
    `receive()` that DOES need network access is resolving a voice/photo `file_id` into a
    downloadable URL (Telegram's Bot API requires a `getFile` call for that) - this goes
    through the same injectable `bot` used by `send_text`/`send_document`.
  - The `aiogram.Bot` instance is constructed lazily (only on first use) and is injectable
    via the constructor's `bot` parameter, mirroring `llm_parser.py`'s `completion_fn`
    injection pattern: tests supply a fake bot instead of hitting Telegram's real API or
    needing a real bot token.
  - Security note: Telegram file download URLs embed the bot token
    (`.../file/bot<TOKEN>/<path>`) - this is how Telegram's Bot API works, not a choice
    made here. Whatever consumes `voice_file_url`/`image_file_url` later (`core/dialog_manager.py`'s
    voice-transcription pipeline / a future Vision path) must not forward these URLs to a
    third-party LLM provider as-is, since that would leak the bot token to that provider -
    audio bytes are downloaded server-side by `core/dialog_manager.py` instead.
  - `send_choice()` renders tappable inline-keyboard buttons (one per option, `callback_data`
    = the option's `value`) so `core/dialog_manager.py` never has to fall back to "type your
    answer" for a fixed set of choices. `receive()` handles the resulting `callback_query`
    update by treating the tapped button's `callback_data` as if it were typed message text -
    same `InboundMessage` shape, so the rest of the pipeline is unaware a button was even
    involved - and acknowledges the tap via `answerCallbackQuery` so Telegram's UI stops
    showing the button's loading spinner.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from messengers.base import InboundMessage, MessengerAdapter, MessengerChannel


class TelegramAdapter(MessengerAdapter):
    """`MessengerAdapter` implementation for Telegram, via aiogram 3.x."""

    channel: MessengerChannel = "telegram"

    def __init__(self, bot_token: str, bot: Any | None = None) -> None:
        if not bot_token:
            raise ValueError("bot_token must be a non-empty Telegram bot token")
        self._bot_token = bot_token
        self._bot = bot  # injected fake in tests; real aiogram.Bot constructed lazily otherwise

    def _get_bot(self) -> Any:
        if self._bot is None:
            from aiogram import Bot  # imported lazily, see module docstring

            self._bot = Bot(token=self._bot_token)
        return self._bot

    async def _resolve_file_url(self, file_id: str) -> str:
        bot = self._get_bot()
        file = await bot.get_file(file_id)
        return f"https://api.telegram.org/file/bot{self._bot_token}/{file.file_path}"

    async def receive(self, raw_payload: dict) -> InboundMessage:
        """Parse a Telegram webhook `Update` JSON payload into an `InboundMessage`.

        Handles `message`, `edited_message`, and `callback_query` (a tapped `send_choice()`
        button) update types (v1 scope). Any other update type (e.g. `channel_post`) raises
        `ValueError` - the caller (`core/dialog_manager.py`) is expected to only route
        message-bearing updates here.
        """
        if "callback_query" in raw_payload:
            return await self._receive_callback_query(raw_payload["callback_query"])

        message = raw_payload.get("message") or raw_payload.get("edited_message")
        if message is None:
            raise ValueError(
                "Unsupported Telegram update: no 'message', 'edited_message', or "
                "'callback_query' field present"
            )

        chat = message.get("chat") or {}
        user_id = str(chat.get("id", ""))
        text = message.get("text") or message.get("caption")

        voice_file_id = None
        if "voice" in message:
            voice_file_id = message["voice"].get("file_id")
        elif "audio" in message:
            voice_file_id = message["audio"].get("file_id")

        image_file_id = None
        if message.get("photo"):
            image_file_id = message["photo"][-1].get("file_id")  # last = largest resolution

        voice_file_url = await self._resolve_file_url(voice_file_id) if voice_file_id else None
        image_file_url = await self._resolve_file_url(image_file_id) if image_file_id else None

        return InboundMessage(
            channel=self.channel,
            user_id=user_id,
            text=text,
            voice_file_url=voice_file_url,
            image_file_url=image_file_url,
        )

    async def _receive_callback_query(self, callback_query: dict) -> InboundMessage:
        """A tapped `send_choice()` button. Normalized to the exact same `InboundMessage`
        shape as a typed reply - `text` is the tapped option's `callback_data` value - so
        `core/dialog_manager.py` treats a tap and a typed answer identically.
        """
        chat = (callback_query.get("message") or {}).get("chat") or {}
        user_id = str(chat.get("id", ""))
        text = callback_query.get("data")

        callback_id = callback_query.get("id")
        if callback_id:
            bot = self._get_bot()
            # Stops the tapped button's loading spinner in the client - purely cosmetic, so a
            # transient failure here should never block processing the actual answer.
            try:
                await bot.answer_callback_query(callback_id)
            except Exception:  # noqa: BLE001 - cosmetic ack, must not fail the whole update
                pass

        return InboundMessage(channel=self.channel, user_id=user_id, text=text)

    async def send_text(self, user_id: str, text: str) -> None:
        bot = self._get_bot()
        await bot.send_message(chat_id=user_id, text=text)

    async def send_choice(self, user_id: str, text: str, options: Sequence[tuple[str, str]]) -> None:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup  # imported lazily

        bot = self._get_bot()
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=label, callback_data=value)] for label, value in options
            ]
        )
        await bot.send_message(chat_id=user_id, text=text, reply_markup=keyboard)

    async def send_document(self, user_id: str, file_path: str | Path, caption: str) -> None:
        from aiogram.types import FSInputFile  # imported lazily, see module docstring

        bot = self._get_bot()
        await bot.send_document(
            chat_id=user_id,
            document=FSInputFile(str(file_path)),
            caption=caption,
        )

    async def ensure_commands_registered(self) -> None:
        """Registers `/start` in Telegram's native "/" command menu so a client always has a
        one-tap way to reset the conversation - independent of whatever the bot last replied
        with (unlike an inline-keyboard button, this stays available even mid-pipeline, e.g.
        while a slow LLM/PDF call is still running). `/start` is already recognized as a reset
        trigger by `core/dialog_manager.py`'s `_RESET_TRIGGERS`; this only makes it discoverable.
        Call once at process startup (`app.py`'s lifespan / `scripts/telegram_polling.py`), not
        per-message - it's a one-time bot-profile setting, not a per-chat action.
        """
        from aiogram.types import BotCommand  # imported lazily, see module docstring

        bot = self._get_bot()
        await bot.set_my_commands(
            [BotCommand(command="start", description="\U0001f504 Zacznij od nowa / restart")]
        )

