"""Cache layer tests -- TTL, write-triggered invalidation, ingestion-triggered invalidation."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from health_metrics.models import DailyMetrics, ManualLog, RegulationCache
from health_metrics.regulation.cache import (
    invalidate_cache,
    read_cache,
    write_cache,
)
from health_metrics.regulation.schemas import (
    DailySnapshot,
    RegulationCall,
    RegulationState,
    SessionBrief,
    TrainingModifier,
)


def _minimal_brief(user_id: str, as_of: date) -> SessionBrief:
    return SessionBrief(
        as_of_date=as_of,
        user_id=user_id,
        regulation_call=RegulationCall(
            state=RegulationState.DEFICIT,
            training_modifier=TrainingModifier.FULL_PROGRESSION,
            kcal_target=2300,
            confidence="high",
        ),
        daily_snapshot=DailySnapshot(user_id=user_id, as_of_date=as_of),
        confidence="high",
        generated_at=datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_read_cache_returns_none_when_no_row(db_session, test_user_id):
    result = await read_cache(db_session, test_user_id, date(2026, 5, 26))
    assert result is None


@pytest.mark.asyncio
async def test_write_then_read_round_trip(db_session, test_user_id):
    brief = _minimal_brief(test_user_id, date(2026, 5, 26))
    await write_cache(db_session, test_user_id, date(2026, 5, 26), brief)
    await db_session.flush()

    got = await read_cache(db_session, test_user_id, date(2026, 5, 26))
    assert got is not None
    assert got.user_id == test_user_id
    assert got.regulation_call.state == RegulationState.DEFICIT
    assert got.regulation_call.kcal_target == 2300


@pytest.mark.asyncio
async def test_cache_invalidated_when_manual_log_newer(db_session, test_user_id):
    """Write cache at T0; insert manual_log at T1 > T0; read returns None."""
    brief = _minimal_brief(test_user_id, date(2026, 5, 26))
    await write_cache(db_session, test_user_id, date(2026, 5, 26), brief)
    await db_session.flush()

    # New manual_log entry post-cache-write
    db_session.add(
        ManualLog(
            user_id=test_user_id,
            log_date=date(2026, 5, 26),
            weight_lbs=Decimal("180"),
        )
    )
    await db_session.flush()

    got = await read_cache(db_session, test_user_id, date(2026, 5, 26))
    assert got is None  # cache is stale


@pytest.mark.asyncio
async def test_invalidate_cache_removes_row(db_session, test_user_id):
    brief = _minimal_brief(test_user_id, date(2026, 5, 26))
    await write_cache(db_session, test_user_id, date(2026, 5, 26), brief)
    await db_session.flush()

    await invalidate_cache(db_session, test_user_id, date(2026, 5, 26))
    await db_session.flush()

    r = await db_session.execute(
        select(RegulationCache).where(
            RegulationCache.user_id == test_user_id,
            RegulationCache.as_of_date == date(2026, 5, 26),
        )
    )
    assert r.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_invalidate_cache_is_noop_when_no_row(db_session, test_user_id):
    """invalidate_cache should not raise if there's nothing to delete."""
    await invalidate_cache(db_session, test_user_id, date(2026, 5, 26))
    await db_session.flush()
    # Confirm: still nothing
    r = await db_session.execute(
        select(RegulationCache).where(
            RegulationCache.user_id == test_user_id,
            RegulationCache.as_of_date == date(2026, 5, 26),
        )
    )
    assert r.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cache_invalidated_when_new_ingestion(db_session, test_user_id):
    """Cache row's latest_ingestion_at < current latest -> stale."""
    # Seed an old daily_metrics row
    db_session.add(
        DailyMetrics(
            user_id=test_user_id,
            metric_date=date(2026, 5, 26),
            ingested_at=datetime(2026, 5, 26, 6, 0, tzinfo=UTC),
        )
    )
    await db_session.flush()

    brief = _minimal_brief(test_user_id, date(2026, 5, 26))
    await write_cache(db_session, test_user_id, date(2026, 5, 26), brief)
    await db_session.flush()

    # Newer ingestion arrives
    db_session.add(
        DailyMetrics(
            user_id=test_user_id,
            metric_date=date(2026, 5, 25),
            ingested_at=datetime(2026, 5, 26, 12, 0, tzinfo=UTC),  # newer than cache's snapshot
        )
    )
    await db_session.flush()

    got = await read_cache(db_session, test_user_id, date(2026, 5, 26))
    assert got is None  # cache stale


@pytest.mark.asyncio
async def test_write_cache_upsert_overwrites_existing(db_session, test_user_id):
    """Second write to the same (user_id, as_of_date) updates rather than dupes."""
    b1 = _minimal_brief(test_user_id, date(2026, 5, 26))
    await write_cache(db_session, test_user_id, date(2026, 5, 26), b1)
    await db_session.flush()

    # Modify and re-write
    b2 = b1.model_copy(update={"confidence": "low"})
    b2.regulation_call.confidence = "low"
    await write_cache(db_session, test_user_id, date(2026, 5, 26), b2)
    await db_session.flush()

    r = await db_session.execute(
        select(RegulationCache).where(
            RegulationCache.user_id == test_user_id,
            RegulationCache.as_of_date == date(2026, 5, 26),
        )
    )
    rows = r.scalars().all()
    assert len(rows) == 1, "should have upserted, not appended"
    got = await read_cache(db_session, test_user_id, date(2026, 5, 26))
    assert got is not None
    assert got.confidence == "low"
