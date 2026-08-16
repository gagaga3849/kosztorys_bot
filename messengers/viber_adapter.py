"""Viber channel adapter (master prompt v1 section 2) — **stub only for v1 scope**.

Telegram is the only channel required for the v1 production Definition of Done (see
`docs/DIARY.md`'s status matrix). This stub exists so:
  - `MessengerChannel`/`MessengerAdapter` prove out as a genuinely channel-agnostic contract
    (three adapters, not just one), and
  - `core/dialog_manager.py`/`app.py` can wire up a Viber webhook route today without waiting
    on a Viber public account/bot registration, then have the real implementation dropped in
    later without touching any other file.

Real implementation (when this channel is actually prioritized) goes through the Viber REST
Bot API: `receive()` would parse Viber's webhook event payload shape (different from both
Telegram's and WhatsApp's), and `send_text`/`send_document` would call Viber's
`send_message` REST endpoint using `VIBER_BOT_TOKEN` (see `.env.example`).
"""

from __future__ import annotations

from pathlib import Path

from messengers.base import InboundMessage, MessengerAdapter, MessengerChannel


class ViberAdapter(MessengerAdapter):
    """Not yet implemented - v1 scope only requires Telegram. See module docstring."""

    channel: MessengerChannel = "viber"

    def __init__(self, bot_token: str) -> None:
        if not bot_token:
            raise ValueError("bot_token must be a non-empty Viber bot token")
        self._bot_token = bot_token

    async def receive(self, raw_payload: dict) -> InboundMessage:
        raise NotImplementedError(
            "ViberAdapter.receive is a v1 stub - Viber is not in the v1 production "
            "Definition of Done. Implement against the Viber REST Bot API webhook event "
            "shape when this channel is prioritized (see module docstring)."
        )

    async def send_text(self, user_id: str, text: str) -> None:
        raise NotImplementedError(
            "ViberAdapter.send_text is a v1 stub - see module docstring."
        )

    async def send_document(self, user_id: str, file_path: str | Path, caption: str) -> None:
        raise NotImplementedError(
            "ViberAdapter.send_document is a v1 stub - see module docstring."
        )
