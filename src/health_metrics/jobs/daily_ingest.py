"""Daily ingest job — fetches Oura + Whoop for a single date and upserts to DB."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import DailyMetrics, OAuthState, Workout
from ..regulation.cache import invalidate_cache
from ..sources.oura import OuraClient
from ..sources.whoop import WhoopAuthError, WhoopClient
from ..transforms.normalize import build_daily_metrics_row
from ..transforms.zscore import compute_zscore

log = structlog.get_logger()

ZSCORE_WINDOW_DAYS = 14


def _build_oura_client() -> OuraClient | None:
    settings = get_settings()
    if not settings.oura_personal_token:
        return None
    return OuraClient(token=settings.oura_personal_token, base_url=settings.oura_base_url)


async def _build_whoop_client(session: AsyncSession, user_id: str) -> WhoopClient | None:
    settings = get_settings()
    if not (settings.whoop_client_id and settings.whoop_client_secret):
        return None

    # Look up live refresh token from oauth_state, fall back to .env
    res = await session.execute(
        select(OAuthState).where(
            OAuthState.provider == "whoop",
            OAuthState.user_id == user_id,
        )
    )
    state = res.scalar_one_or_none()
    refresh_token = state.refresh_token if state else settings.whoop_refresh_token
    if not refresh_token:
        return None

    async def _persist(access: str, refresh: str, expires_at: datetime) -> None:
        stmt = pg_insert(OAuthState).values(
            provider="whoop",
            user_id=user_id,
            refresh_token=refresh,
            access_token=access,
            access_expires_at=expires_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider", "user_id"],
            set_={
                "refresh_token": refresh,
                "access_token": access,
                "access_expires_at": expires_at,
            },
        )
        await session.execute(stmt)
        # Commit immediately so the rotation is durable even if the rest of
        # the ingest fails. Without this, a failure post-refresh would lose
        # the new refresh_token and lock us out of Whoop.
        await session.commit()

    return WhoopClient(
        access_token=state.access_token if state and state.access_token else "",
        refresh_token=refresh_token,
        client_id=settings.whoop_client_id,
        client_secret=settings.whoop_client_secret,
        base_url=settings.whoop_base_url,
        oauth_url=settings.whoop_oauth_url,
        on_token_refresh=_persist,
    )


async def run_daily_ingest(
    day: date,
    user_id: str,
    session: AsyncSession,
    commit: bool = True,
) -> dict[str, Any]:
    """
    Pull Oura + Whoop for `day`, upsert to daily_metrics + workouts, recompute z-scores.

    Args:
        day: The target date to ingest.
        user_id: The user identifier for DB rows.
        session: SQLAlchemy async session to use.
        commit: If True, commit the transaction. If False, flush only (safe inside
                test fixtures that wrap in an outer rollback transaction).

    Returns a small status dict for logging/HTTP responses.
    """
    oura_status = "skipped"
    whoop_status = "skipped"
    oura_payload = None
    whoop_payload = None
    whoop_workouts: list = []

    oura_client = _build_oura_client()
    if oura_client is not None:
        try:
            oura_payload = await oura_client.fetch_day(day)
            oura_status = "ok"
        except Exception as e:
            log.warning("oura_fetch_failed", day=day.isoformat(), error=str(e))
            oura_status = "failed"
        finally:
            await oura_client.close()

    whoop_client = await _build_whoop_client(session, user_id)
    if whoop_client is not None:
        try:
            whoop_payload, whoop_workouts = await whoop_client.fetch_day(day)
            whoop_status = "ok"
        except WhoopAuthError as e:
            # Token refresh failed (e.g. revoked/expired refresh token). Record a
            # distinct status + log at error level so it is not silently masked as
            # 'ok' with empty data — the brief surfaces this as a re-auth prompt.
            log.error("whoop_auth_failed", day=day.isoformat(), error=str(e))
            whoop_status = "auth_error"
        except Exception as e:
            log.warning("whoop_fetch_failed", day=day.isoformat(), error=str(e))
            whoop_status = "failed"
        finally:
            await whoop_client.close()

    if oura_payload is None and whoop_payload is None:
        # Build a synthetic row anchored on the requested date so the
        # downstream upsert and z-score recompute have something to operate on.
        row: dict[str, Any] = {
            "user_id": user_id,
            "metric_date": day,
            "oura_status": oura_status,
            "whoop_status": whoop_status,
            "ingestion_complete": False,
        }
    else:
        row = build_daily_metrics_row(
            user_id=user_id,
            oura=oura_payload,
            whoop=whoop_payload,
            oura_status=oura_status,
            whoop_status=whoop_status,
        )

    stmt = pg_insert(DailyMetrics).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "metric_date"],
        set_={k: v for k, v in row.items() if k not in ("user_id", "metric_date")},
    )
    await session.execute(stmt)

    for w in whoop_workouts:
        # Parse started_at string to datetime if needed
        started_at = w.started_at
        if isinstance(started_at, str):
            # Handle ISO 8601 strings, e.g. "2026-05-12T17:00:00.000Z"
            started_at = started_at.replace("Z", "+00:00")
            started_at = datetime.fromisoformat(started_at)

        w_row = {
            "user_id": user_id,
            "workout_date": w.workout_date,
            "source": "whoop",
            "source_id": w.source_id,
            "workout_type": w.workout_type,
            "started_at": started_at,
            "duration_min": w.duration_min,
            "avg_hr": w.avg_hr,
            "max_hr": w.max_hr,
            "strain": w.strain,
            "kcal": w.kcal,
            "zone_0_min": w.zone_minutes.get(0),
            "zone_1_min": w.zone_minutes.get(1),
            "zone_2_min": w.zone_minutes.get(2),
            "zone_3_min": w.zone_minutes.get(3),
            "zone_4_min": w.zone_minutes.get(4),
            "zone_5_min": w.zone_minutes.get(5),
            "raw": w.raw,
        }
        stmt = pg_insert(Workout).values(**w_row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_id"],
            set_={k: v for k, v in w_row.items() if k not in ("source", "source_id")},
        )
        await session.execute(stmt)

    await _recompute_zscores(session, user_id=user_id, anchor_day=day)

    # Bust today's brief cache so a fresh ingest — via the scheduler OR the manual
    # /ingest/daily trigger — never leaves a stale session-brief. Pre-commit so it
    # rides the same transaction and respects the commit flag.
    await invalidate_cache(session, user_id, date.today())

    if commit:
        await session.commit()
    else:
        await session.flush()

    log.info(
        "daily_ingest_complete",
        day=day.isoformat(),
        user_id=user_id,
        oura_status=oura_status,
        whoop_status=whoop_status,
        workouts=len(whoop_workouts),
    )
    return {
        "day": day.isoformat(),
        "user_id": user_id,
        "oura_status": oura_status,
        "whoop_status": whoop_status,
        "workout_count": len(whoop_workouts),
    }


async def _recompute_zscores(session: AsyncSession, user_id: str, anchor_day: date) -> None:
    """
    For each day in [anchor - 13, anchor], recompute hrv/rhr/sleep z-scores
    against the trailing 14-day baseline (excluding the day being scored).

    With <7 baseline values, the z-score is NULL — that's the cold-start case.
    """
    window_start = anchor_day - timedelta(days=ZSCORE_WINDOW_DAYS - 1)
    res = await session.execute(
        select(DailyMetrics)
        .where(DailyMetrics.user_id == user_id)
        .where(DailyMetrics.metric_date >= window_start - timedelta(days=ZSCORE_WINDOW_DAYS))
        .where(DailyMetrics.metric_date <= anchor_day)
        .order_by(DailyMetrics.metric_date.asc())
    )
    all_rows = list(res.scalars().all())

    for row in all_rows:
        target_date = row.metric_date
        if target_date < window_start:
            continue

        baseline_hrv: list[float] = []
        baseline_rhr: list[float] = []
        baseline_sleep: list[float] = []
        for r in all_rows:
            if r.metric_date >= target_date:
                continue
            if r.metric_date < target_date - timedelta(days=ZSCORE_WINDOW_DAYS):
                continue
            hrv = r.oura_hrv_avg if r.oura_hrv_avg is not None else r.whoop_hrv_ms
            if hrv is not None:
                baseline_hrv.append(float(hrv))
            rhr = r.oura_rhr if r.oura_rhr is not None else r.whoop_rhr
            if rhr is not None:
                baseline_rhr.append(float(rhr))
            if r.oura_sleep_duration_min is not None:
                baseline_sleep.append(float(r.oura_sleep_duration_min))

        cur_hrv = row.oura_hrv_avg if row.oura_hrv_avg is not None else row.whoop_hrv_ms
        if cur_hrv is not None:
            z = compute_zscore(float(cur_hrv), baseline_hrv)
            row.unified_hrv_z = Decimal(f"{z:.2f}") if z is not None else None

        cur_rhr = row.oura_rhr if row.oura_rhr is not None else row.whoop_rhr
        if cur_rhr is not None:
            z = compute_zscore(float(cur_rhr), baseline_rhr)
            row.unified_rhr_z = Decimal(f"{z:.2f}") if z is not None else None

        if row.oura_sleep_duration_min is not None:
            z = compute_zscore(float(row.oura_sleep_duration_min), baseline_sleep)
            row.unified_sleep_z = Decimal(f"{z:.2f}") if z is not None else None
