"""Cache layer for SessionBrief reads.

Validity rule (spec §6 + Invariant #3):
  cache is fresh IFF
      cache.latest_ingestion_at >= latest_ingested_at(user_id) AND
      cache.latest_write_at >= latest_write_at(user_id, as_of_date)

  where:
    latest_ingested_at(user_id) = MAX(ingested_at) on daily_metrics for user
    latest_write_at(user_id, d) = MAX(updated_at) across manual_log + meals
                                  + health_events for that user on that date

If invalid, recompute + write back.
"""

from datetime import date as date_type, datetime, timezone

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DailyMetrics, HealthEvent, ManualLog, Meal, RegulationCache
from .schemas import SessionBrief

log = structlog.get_logger()


async def get_latest_ingestion_at(
    session: AsyncSession, user_id: str
) -> datetime | None:
    """MAX(ingested_at) on daily_metrics for the user. None if no rows exist."""
    r = await session.execute(
        select(func.max(DailyMetrics.ingested_at)).where(
            DailyMetrics.user_id == user_id
        )
    )
    return r.scalar_one_or_none()


async def get_latest_write_at(
    session: AsyncSession, user_id: str, as_of: date_type
) -> datetime:
    """MAX(updated_at) across manual_log + meals + health_events for the user.

    manual_log + meals scoped to as_of date; health_events not date-scoped
    (any event update can change the brief). Returns epoch if no rows.
    """
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    candidates: list[datetime] = [epoch]

    r = await session.execute(
        select(func.max(ManualLog.updated_at)).where(
            ManualLog.user_id == user_id, ManualLog.log_date == as_of
        )
    )
    v = r.scalar_one_or_none()
    if v is not None:
        candidates.append(v)

    # Meal has no updated_at; use created_at (Meal rows are append-only).
    r = await session.execute(
        select(func.max(Meal.created_at)).where(
            Meal.user_id == user_id, Meal.meal_date == as_of
        )
    )
    v = r.scalar_one_or_none()
    if v is not None:
        candidates.append(v)

    r = await session.execute(
        select(func.max(HealthEvent.updated_at)).where(
            HealthEvent.user_id == user_id
        )
    )
    v = r.scalar_one_or_none()
    if v is not None:
        candidates.append(v)

    return max(candidates)


async def read_cache(
    session: AsyncSession, user_id: str, as_of: date_type
) -> SessionBrief | None:
    """Returns the cached SessionBrief if fresh; None otherwise."""
    r = await session.execute(
        select(RegulationCache).where(
            RegulationCache.user_id == user_id,
            RegulationCache.as_of_date == as_of,
        )
    )
    row = r.scalar_one_or_none()
    if row is None:
        return None

    latest_ing = await get_latest_ingestion_at(session, user_id)
    latest_wr = await get_latest_write_at(session, user_id, as_of)

    fresh = (
        latest_ing is None or row.latest_ingestion_at >= latest_ing
    ) and row.latest_write_at >= latest_wr
    if not fresh:
        log.info(
            "regulation_cache_stale",
            user_id=user_id,
            as_of=as_of.isoformat(),
        )
        return None

    return SessionBrief.model_validate(row.brief_json)


async def write_cache(
    session: AsyncSession,
    user_id: str,
    as_of: date_type,
    brief: SessionBrief,
) -> None:
    """Idempotent upsert -- record latest_ingestion_at + latest_write_at as of NOW."""
    latest_ing = await get_latest_ingestion_at(session, user_id) or datetime(
        1970, 1, 1, tzinfo=timezone.utc
    )
    latest_wr = await get_latest_write_at(session, user_id, as_of)
    brief_payload = brief.model_dump(mode="json")

    stmt = (
        pg_insert(RegulationCache)
        .values(
            user_id=user_id,
            as_of_date=as_of,
            brief_json=brief_payload,
            latest_ingestion_at=latest_ing,
            latest_write_at=latest_wr,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "as_of_date"],
            set_={
                "brief_json": brief_payload,
                "latest_ingestion_at": latest_ing,
                "latest_write_at": latest_wr,
                "cached_at": func.now(),
            },
        )
    )
    await session.execute(stmt)


async def invalidate_cache(
    session: AsyncSession, user_id: str, as_of: date_type
) -> None:
    """Delete the cache row so the next read recomputes.

    Called from PR 4's write endpoints when manual_log / meals / health_events
    change.
    """
    await session.execute(
        delete(RegulationCache).where(
            RegulationCache.user_id == user_id,
            RegulationCache.as_of_date == as_of,
        )
    )
