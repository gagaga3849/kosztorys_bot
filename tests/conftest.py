"""Shared pytest fixtures.

`db_engine`/`db_session` are integration fixtures for `tests/test_price_repository.py`. They
connect to `TEST_DATABASE_URL` (falls back to a sane local default matching the docs/DIARY.md
"how to run tests" instructions) and `pytest.skip(...)` the whole session if no Postgres is
reachable - so `pytest -q` still runs cleanly on a machine with no DB configured. CI always has
a Postgres service container available, so these tests are not CI-skipped there (see the
GitHub Actions workflow planned in docs/DIARY.md's Phase D).
"""

from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

from db.models import Base
from db.session import create_engine

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://postgres:test@localhost:55432/kosztorys_test"


def _test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


async def _can_connect(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect():
            return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def db_engine():
    engine = create_engine(_test_database_url())
    if not await _can_connect(engine):
        await engine.dispose()
        pytest.skip(f"No test Postgres reachable at {_test_database_url()!r} - skipping integration tests")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine):
    from db.session import create_session_factory

    factory = create_session_factory(db_engine)
    async with factory() as session:
        yield session
