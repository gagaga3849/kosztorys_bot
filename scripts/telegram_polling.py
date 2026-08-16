"""Local/dev tool: chat with the real Telegram bot via long polling, without needing a
public HTTPS URL, `setWebhook` call, or ngrok tunnel.

Not part of the master prompt's 8-step file order or the production Definition of Done -
`app.py`'s webhook route is what actually ships. This script exists purely so the bot can be
exercised end-to-end (Telegram -> LLM parse -> calculator -> PDF -> Telegram) from a laptop
during development/demos.

It reuses the exact same production wiring as `app.py`'s `lifespan` (`Settings.from_env()`,
`db.session`, `price_repository.load_price_repository`, `app._build_adapters`,
`core.dialog_manager.DialogManager`) and calls the exact same
`DialogManager.handle_webhook("telegram", raw_update)` entry point the webhook route uses -
only the transport (long polling instead of an inbound POST) differs.

Usage:
    source .venv/bin/activate
    python scripts/telegram_polling.py

Requires the same `.env` as `app.py`: at minimum `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, and
`LLM_MODEL` (+ the matching provider API key, e.g. `GROQ_API_KEY`).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app import _build_adapters
from config import Settings
from core.dialog_manager import DialogManager
from db.session import create_engine, session_scope
from messengers.telegram_adapter import TelegramAdapter
from price_repository import load_price_repository

logger = logging.getLogger("kosztorys_bot.polling")

POLL_TIMEOUT_SECONDS = 30


def _log_task_exception(task: asyncio.Task, update_id: int) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Failed to process update_id=%s", update_id, exc_info=exc)


async def _poll_loop(dialog_manager: DialogManager, bot_token: str) -> None:
    base_url = f"https://api.telegram.org/bot{bot_token}"
    # Generous headroom over Telegram's own long-poll timeout - Telegram may legitimately hold
    # the connection open for close to POLL_TIMEOUT_SECONDS before replying with an empty
    # result, plus network/TLS latency on top of that.
    async with httpx.AsyncClient(timeout=POLL_TIMEOUT_SECONDS + 30) as client:
        # Long polling (getUpdates) and webhooks are mutually exclusive on Telegram's side -
        # drop any webhook that might be configured on this token before polling.
        await client.post(f"{base_url}/deleteWebhook")

        offset = 0
        logger.info("Long-polling started - open Telegram and message your bot now.")
        while True:
            try:
                response = await client.get(
                    f"{base_url}/getUpdates",
                    params={"offset": offset, "timeout": POLL_TIMEOUT_SECONDS},
                )
                response.raise_for_status()
                payload = response.json()
            except httpx.TimeoutException:
                # Expected when no message arrives within the long-poll window - not an error.
                continue
            except httpx.HTTPError:
                logger.exception("getUpdates request failed, retrying in 2s")
                await asyncio.sleep(2)
                continue

            for update in payload.get("result", []):
                offset = update["update_id"] + 1
                logger.info("Received update_id=%s", update["update_id"])
                # Fire-and-forget per update (not `await`ed inline) - a slow/hung external call
                # (LLM, PDF render, Telegram upload) for one update must never block the next
                # `getUpdates` iteration, otherwise even a "/start" reset from the client can't
                # be picked up until the earlier update finishes. Matches production's `app.py`
                # webhook route, where FastAPI already handles each request as its own task.
                task = asyncio.create_task(dialog_manager.handle_webhook("telegram", update))
                task.add_done_callback(
                    lambda t, uid=update["update_id"]: _log_task_exception(t, uid)
                )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    engine = create_engine(settings.database_url)
    try:
        async with session_scope(engine) as session:
            prices = await load_price_repository(session)
        adapters = _build_adapters(settings)
        dialog_manager = DialogManager(
            adapters=adapters,
            prices=prices,
            output_dir=settings.output_dir,
            admin_channel=settings.admin_channel,
            admin_user_id=settings.admin_user_id,
        )
        telegram_adapter = adapters.get("telegram")
        if isinstance(telegram_adapter, TelegramAdapter):
            await telegram_adapter.ensure_commands_registered()
        await _poll_loop(dialog_manager, settings.telegram_bot_token)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
