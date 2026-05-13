import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_db_session_works(db_session):
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_db_session_can_read_daily_metrics(db_session):
    """Confirms tables from Task 6 migration are accessible."""
    result = await db_session.execute(text("SELECT COUNT(*) FROM daily_metrics"))
    assert result.scalar() == 0
