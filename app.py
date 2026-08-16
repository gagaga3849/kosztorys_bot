"""FastAPI assembly (master prompt section 6, step 8 - the final generation step): mounts
one webhook route per channel onto a single ASGI app/process (master prompt section 2: three
adapters, not three processes), and wires real dependencies (Postgres-backed
`PriceRepository`, real `MessengerAdapter`s, `core.dialog_manager.DialogManager`) together.

Two ways to get an app, matching the project's DI-for-testability convention:
  - `create_app(dialog_manager=<fake or real>)` - the test/injection path. No lifespan, no
    real DB/network touched; used by `tests/test_app.py` with an in-memory fake
    `DialogManager`-like object.
  - `create_app()` with no arguments - the production path (what `app = create_app()` below
    gives `uvicorn app:app`). Registers a `lifespan` that builds the real `Settings`,
    Postgres engine, `PriceRepository` snapshot and `MessengerAdapter`s only once the ASGI
    server's event loop is actually running, not at import time.

Security notes (OWASP-relevant, applied here):
  - The Telegram webhook route verifies the `X-Telegram-Bot-Api-Secret-Token` header against
    `TELEGRAM_WEBHOOK_SECRET` (when configured) using `secrets.compare_digest` (constant-time
    comparison) before processing anything, so an attacker can't POST fake messages/trigger
    LLM calls and PDF generation for free. Must be set as the `secret_token` when calling
    Telegram's `setWebhook` - see `.env.example`.
  - Processing errors inside a webhook handler are logged server-side and never reflected
    back into the HTTP response body (no stack traces / internal details leaked to callers).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, Response

from config import Settings
from core.dialog_manager import DialogManager
from db.session import create_engine, session_scope
from messengers.base import MessengerAdapter, MessengerChannel
from messengers.telegram_adapter import TelegramAdapter
from messengers.viber_adapter import ViberAdapter
from messengers.whatsapp_adapter import WhatsAppAdapter
from price_repository import load_price_repository

logger = logging.getLogger("kosztorys_bot.app")


def _build_adapters(settings: Settings) -> dict[MessengerChannel, MessengerAdapter]:
    """Telegram is always wired (v1's only required channel). WhatsApp/Viber are wired only
    if their credentials are configured - otherwise their webhook routes respond 501 rather
    than constructing an adapter that would immediately raise `NotImplementedError` anyway.
    """
    adapters: dict[MessengerChannel, MessengerAdapter] = {
        "telegram": TelegramAdapter(bot_token=settings.telegram_bot_token),
    }
    if settings.whatsapp_api_token and settings.whatsapp_phone_number_id:
        adapters["whatsapp"] = WhatsAppAdapter(
            api_token=settings.whatsapp_api_token,
            phone_number_id=settings.whatsapp_phone_number_id,
        )
    if settings.viber_bot_token:
        adapters["viber"] = ViberAdapter(bot_token=settings.viber_bot_token)
    return adapters


async def _handle_webhook(app: FastAPI, channel: MessengerChannel, request: Request) -> Response:
    payload = await request.json()
    dialog_manager: DialogManager = app.state.dialog_manager
    try:
        await dialog_manager.handle_webhook(channel, payload)
    except ValueError:
        # Channel not configured at all (e.g. WhatsApp/Viber credentials absent).
        raise HTTPException(status_code=501, detail=f"{channel} channel is not configured")
    except NotImplementedError:
        # Channel configured but its adapter is still a v1 stub (WhatsApp/Viber).
        raise HTTPException(status_code=501, detail=f"{channel} channel is not implemented yet")
    except Exception:
        # Never leak internal errors to the caller; Telegram/webhook senders should still get
        # a 200 so they don't hammer us with retries - the failure is logged for us to see.
        logger.exception("Unhandled error processing %s webhook", channel)
        return Response(status_code=200)
    return Response(status_code=200)


def create_app(
    dialog_manager: DialogManager | None = None,
    webhook_secret: str | None = None,
) -> FastAPI:
    if dialog_manager is not None:
        app = FastAPI(title="Kosztorys Bot")
        app.state.dialog_manager = dialog_manager
        app.state.webhook_secret = webhook_secret
    else:

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            settings = Settings.from_env()
            engine = create_engine(settings.database_url)
            async with session_scope(engine) as session:
                prices = await load_price_repository(session)
            adapters = _build_adapters(settings)
            app.state.dialog_manager = DialogManager(
                adapters=adapters,
                prices=prices,
                output_dir=settings.output_dir,
                admin_channel=settings.admin_channel,
                admin_user_id=settings.admin_user_id,
            )
            app.state.webhook_secret = settings.telegram_webhook_secret
            telegram_adapter = adapters.get("telegram")
            if isinstance(telegram_adapter, TelegramAdapter):
                await telegram_adapter.ensure_commands_registered()
            try:
                yield
            finally:
                await engine.dispose()

        app = FastAPI(title="Kosztorys Bot", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/webhook/telegram")
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> Response:
        expected = app.state.webhook_secret
        if expected and not secrets.compare_digest(x_telegram_bot_api_secret_token or "", expected):
            raise HTTPException(status_code=403, detail="invalid webhook secret token")
        return await _handle_webhook(app, "telegram", request)

    @app.post("/webhook/whatsapp")
    async def whatsapp_webhook(request: Request) -> Response:
        return await _handle_webhook(app, "whatsapp", request)

    @app.post("/webhook/viber")
    async def viber_webhook(request: Request) -> Response:
        return await _handle_webhook(app, "viber", request)

    return app


app = create_app()
