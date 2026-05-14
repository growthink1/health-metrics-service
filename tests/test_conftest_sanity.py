import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_db_session_works(db_session):
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


SENTINEL_USER = "test_rollback_sentinel"


@pytest.mark.asyncio
async def test_db_session_inserts_visible_within_session(db_session):
    """Insert a sentinel row and confirm we can read it back within the same session."""
    await db_session.execute(
        text(
            "INSERT INTO daily_metrics (user_id, metric_date, oura_status, whoop_status) "
            "VALUES (:u, '2026-01-01', 'ok', 'ok')"
        ),
        {"u": SENTINEL_USER},
    )
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM daily_metrics WHERE user_id = :u"),
        {"u": SENTINEL_USER},
    )
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_db_session_rollback_isolates_between_tests(db_session):
    """After the prior test's teardown, no sentinel rows should remain."""
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM daily_metrics WHERE user_id = :u"),
        {"u": SENTINEL_USER},
    )
    assert result.scalar() == 0
