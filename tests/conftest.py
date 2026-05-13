"""Pytest fixtures — async DB session with per-test rollback."""

from typing import AsyncIterator

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from health_metrics.config import get_settings


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session that rolls back on teardown.

    Creates a fresh engine per test to avoid event loop conflicts,
    wraps in a transaction, and rolls back at teardown.
    """
    # Clear the settings cache to ensure we get the real DB settings,
    # not cached test settings from other tests
    get_settings.cache_clear()
    settings = get_settings()

    engine = create_async_engine(settings.database_url, echo=False, future=True)

    # Register begin/commit handlers to manage transactions
    @event.listens_for(engine.sync_engine, "connect")
    def receive_connect(dbapi_conn, connection_record):
        dbapi_conn.isolation_level = None

    try:
        connection = await engine.connect()
        transaction = await connection.begin()

        SessionLocal = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        session = SessionLocal()

        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
            await connection.close()
    finally:
        await engine.dispose()
