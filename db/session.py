"""Async SQLAlchemy engine/session factory.

Reads `DATABASE_URL` from the environment (e.g.
`postgresql+asyncpg://user:pass@host:5432/kosztorys`). No default production URL is baked in -
missing configuration must fail loudly at startup (`config.py`, a later step, will enforce this
via `pydantic-settings`); this module only fails loudly if actually used without the env var set.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Example: "
            "postgresql+asyncpg://kosztorys:kosztorys@localhost:5432/kosztorys"
        )
    return url


def create_engine(database_url: str | None = None) -> AsyncEngine:
    return create_async_engine(database_url or get_database_url(), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One-shot session for scripts/tests. `app.py` (a later step) will manage its own
    request-scoped sessions via a FastAPI dependency instead of this helper."""
    factory = create_session_factory(engine)
    async with factory() as session:
        yield session
