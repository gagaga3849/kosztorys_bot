"""Environment-based configuration (master prompt section 5's `.env.example`).

Convention: read env vars once via `Settings.from_env()`, called only from `app.py`'s
lifespan (i.e. at process startup, not at import time) - the same "fail loud only when
actually used" rule already used by `db/session.py`'s `get_database_url()`, so importing
this module never requires every env var to already be set (tests stay import-safe).

`python-dotenv` is used so a local `.env` file (see `.env.example`) can populate `os.environ`
during development; in production the real process environment is expected to already be set
(container/orchestrator secrets), so a missing `.env` file is not an error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from messengers.base import MessengerChannel

_VALID_CHANNELS: tuple[MessengerChannel, ...] = ("telegram", "whatsapp", "viber")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set - see .env.example")
    return value


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    database_url: str
    output_dir: str
    admin_channel: MessengerChannel | None
    admin_user_id: str | None
    telegram_webhook_secret: str | None
    whatsapp_api_token: str | None
    whatsapp_phone_number_id: str | None
    viber_bot_token: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        """Loads a local `.env` file (if present) into `os.environ`, then reads settings.
        Raises `RuntimeError` immediately for anything required but missing/malformed -
        never falls back to a silently-wrong default for security- or money-relevant config.
        """
        from dotenv import load_dotenv  # imported lazily, mirrors project's DI convention

        load_dotenv()

        admin_channel_raw = os.environ.get("ADMIN_CHANNEL") or None
        if admin_channel_raw is not None and admin_channel_raw not in _VALID_CHANNELS:
            raise RuntimeError(
                f"ADMIN_CHANNEL={admin_channel_raw!r} is not one of {_VALID_CHANNELS}"
            )

        return cls(
            telegram_bot_token=_require_env("TELEGRAM_BOT_TOKEN"),
            database_url=_require_env("DATABASE_URL"),
            output_dir=os.environ.get("OUTPUT_DIR", "./output"),
            admin_channel=admin_channel_raw,  # type: ignore[arg-type]
            admin_user_id=os.environ.get("ADMIN_USER_ID") or None,
            telegram_webhook_secret=os.environ.get("TELEGRAM_WEBHOOK_SECRET") or None,
            whatsapp_api_token=os.environ.get("WHATSAPP_CLOUD_API_TOKEN") or None,
            whatsapp_phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or None,
            viber_bot_token=os.environ.get("VIBER_BOT_TOKEN") or None,
        )
