"""Channel-agnostic messenger contract (master prompt section 2: "мультиканальность").

Architectural rule: the messenger is transport, not architecture. `core/dialog_manager.py`
(not yet built) receives one `InboundMessage`, regardless of whether it came from Telegram,
WhatsApp, or Viber, and replies through the same `MessengerAdapter` interface. None of the
business logic (`llm_parser.py` -> `calculator.py` -> `pdf_generator.py`) ever branches on
`channel` - only the adapters themselves know about their platform's webhook payload shape,
auth, and outbound API calls.

Each concrete adapter (`telegram_adapter.py`, `whatsapp_adapter.py`, `viber_adapter.py`) is
responsible for:
  - exposing its own FastAPI `APIRouter` for its webhook route (mounted onto the single
    shared `app.py`, not a separate process per channel);
  - translating its platform's raw webhook payload into an `InboundMessage` via `receive()`;
  - sending replies back out via `send_text()`/`send_document()`.

v1 scope note: Telegram is the only channel required for the production Definition of Done.
WhatsApp/Viber adapters exist as stubs (raising `NotImplementedError`) so the shared
interface and `app.py` wiring are provable in tests without needing sandbox accounts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

MessengerChannel = Literal["telegram", "whatsapp", "viber"]


class InboundMessage(BaseModel):
    """Normalized representation of one incoming message, regardless of channel.

    `text`/`voice_file_url`/`image_file_url` are all optional and independent - a message
    may carry any combination of them (e.g. a photo with a caption). It is `llm_parser.py`'s
    job, not this model's, to decide what to do when all three are empty.
    """

    channel: MessengerChannel
    user_id: str
    text: str | None = None
    voice_file_url: str | None = None
    image_file_url: str | None = None


class MessengerAdapter(ABC):
    """Abstract contract every channel adapter must implement.

    `channel` identifies which `MessengerChannel` this adapter serves - concrete adapters
    set it as a class attribute (see `telegram_adapter.py`) so `app.py` can log/route by
    channel without importing each adapter module by name.
    """

    channel: MessengerChannel

    @abstractmethod
    async def receive(self, raw_payload: dict) -> InboundMessage:
        """Translate this platform's raw webhook payload into an `InboundMessage`."""
        raise NotImplementedError

    @abstractmethod
    async def send_text(self, user_id: str, text: str) -> None:
        """Send a plain text reply to `user_id` on this channel."""
        raise NotImplementedError

    @abstractmethod
    async def send_document(self, user_id: str, file_path: str | Path, caption: str) -> None:
        """Send a document (e.g. the generated Kosztorys PDF) with a caption to `user_id`."""
        raise NotImplementedError
