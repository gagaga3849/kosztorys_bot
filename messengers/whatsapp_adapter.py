"""WhatsApp channel adapter (master prompt v1 section 2) — **stub only for v1 scope**.

Telegram is the only channel required for the v1 production Definition of Done (see
`docs/DIARY.md`'s status matrix). This stub exists so:
  - `MessengerChannel`/`MessengerAdapter` prove out as a genuinely channel-agnostic contract
    (three adapters, not just one), and
  - `core/dialog_manager.py`/`app.py` can wire up a WhatsApp webhook route today without
    waiting on a Meta WhatsApp Cloud API sandbox account, then have the real implementation
    dropped in later without touching any other file.

Real implementation (when this channel is actually prioritized) goes through the WhatsApp
Cloud API (Meta): `receive()` would parse the Cloud API's webhook payload shape (different
from Telegram's), and `send_text`/`send_document` would call the Cloud API's `/messages`
endpoint using `WHATSAPP_CLOUD_API_TOKEN`/`WHATSAPP_PHONE_NUMBER_ID` (see `.env.example`).
"""

from __future__ import annotations

from pathlib import Path

from messengers.base import InboundMessage, MessengerAdapter, MessengerChannel


class WhatsAppAdapter(MessengerAdapter):
    """Not yet implemented - v1 scope only requires Telegram. See module docstring."""

    channel: MessengerChannel = "whatsapp"

    def __init__(self, api_token: str, phone_number_id: str) -> None:
        if not api_token or not phone_number_id:
            raise ValueError("api_token and phone_number_id must both be non-empty")
        self._api_token = api_token
        self._phone_number_id = phone_number_id

    async def receive(self, raw_payload: dict) -> InboundMessage:
        raise NotImplementedError(
            "WhatsAppAdapter.receive is a v1 stub - WhatsApp is not in the v1 production "
            "Definition of Done. Implement against the WhatsApp Cloud API webhook payload "
            "shape when this channel is prioritized (see module docstring)."
        )

    async def send_text(self, user_id: str, text: str) -> None:
        raise NotImplementedError(
            "WhatsAppAdapter.send_text is a v1 stub - see module docstring."
        )

    async def send_document(self, user_id: str, file_path: str | Path, caption: str) -> None:
        raise NotImplementedError(
            "WhatsAppAdapter.send_document is a v1 stub - see module docstring."
        )
