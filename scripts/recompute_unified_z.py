"""Recompute unified_{hrv,rhr,sleep}_z over the canonical 14-day rolling window
with 7-day warmup -- the window locked in spec §10 decision #1.

Verified during PR 2 implementation: this matches the window already used by
src/health_metrics/jobs/daily_ingest.py::_recompute_zscores (ZSCORE_WINDOW_DAYS=14)
and src/health_metrics/transforms/zscore.py (MIN_BASELINE=7). The script exists
to (a) provide a one-shot recomputation hook in case any prior rows were written
with a different window, and (b) document the canonical window in scripts/.

Usage:
    # Dry-run (default): prints how many rows would change, no DB writes.
    DATABASE_URL=postgresql+asyncpg://... python scripts/recompute_unified_z.py

    # Apply: actually UPDATE the rows.
    DATABASE_URL=postgresql+asyncpg://... python scripts/recompute_unified_z.py --apply

    # Restrict to one user:
    python scripts/recompute_unified_z.py --user hugo --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from health_metrics.models import DailyMetrics
from health_metrics.transforms.zscore import compute_zscore

ZSCORE_WINDOW_DAYS = 14
DEFAULT_USERS = ("hugo", "andrea")


def _baseline_values(
    rows: list[DailyMetrics],
    target_date: date,
    selector: str,
) -> list[float]:
    """Pull baseline values from the rolling window strictly before target_date."""
    earliest = target_date - timedelta(days=ZSCORE_WINDOW_DAYS)
    out: list[float] = []
    for r in rows:
        if r.metric_date >= target_date or r.metric_date < earliest:
            continue
        if selector == "hrv":
            v = r.oura_hrv_avg if r.oura_hrv_avg is not None else r.whoop_hrv_ms
        elif selector == "rhr":
            v = r.oura_rhr if r.oura_rhr is not None else r.whoop_rhr
        elif selector == "sleep":
            v = r.oura_sleep_duration_min
        else:
            raise ValueError(f"unknown selector {selector!r}")
        if v is not None:
            out.append(float(v))
    return out


def _current_value(row: DailyMetrics, selector: str) -> float | None:
    if selector == "hrv":
        v = row.oura_hrv_avg if row.oura_hrv_avg is not None else row.whoop_hrv_ms
    elif selector == "rhr":
        v = row.oura_rhr if row.oura_rhr is not None else row.whoop_rhr
    elif selector == "sleep":
        v = row.oura_sleep_duration_min
    else:
        raise ValueError(f"unknown selector {selector!r}")
    return float(v) if v is not None else None


def _as_decimal(z: float | None) -> Decimal | None:
    return Decimal(f"{z:.2f}") if z is not None else None


async def _recompute_for_user(
    session: AsyncSession,
    user_id: str,
    apply: bool,
) -> tuple[int, int]:
    """Returns (rows_examined, rows_changed)."""
    res = await session.execute(
        select(DailyMetrics).where(DailyMetrics.user_id == user_id).order_by(DailyMetrics.metric_date.asc())
    )
    rows = list(res.scalars().all())

    changed = 0
    for row in rows:
        new_hrv = (
            compute_zscore(
                _current_value(row, "hrv") or 0.0,
                _baseline_values(rows, row.metric_date, "hrv"),
            )
            if _current_value(row, "hrv") is not None
            else None
        )
        new_rhr = (
            compute_zscore(
                _current_value(row, "rhr") or 0.0,
                _baseline_values(rows, row.metric_date, "rhr"),
            )
            if _current_value(row, "rhr") is not None
            else None
        )
        new_sleep = (
            compute_zscore(
                _current_value(row, "sleep") or 0.0,
                _baseline_values(rows, row.metric_date, "sleep"),
            )
            if _current_value(row, "sleep") is not None
            else None
        )

        new_hrv_d = _as_decimal(new_hrv)
        new_rhr_d = _as_decimal(new_rhr)
        new_sleep_d = _as_decimal(new_sleep)

        row_changed = (
            row.unified_hrv_z != new_hrv_d or row.unified_rhr_z != new_rhr_d or row.unified_sleep_z != new_sleep_d
        )
        if row_changed:
            changed += 1
            if apply:
                row.unified_hrv_z = new_hrv_d
                row.unified_rhr_z = new_rhr_d
                row.unified_sleep_z = new_sleep_d

    if apply:
        await session.commit()

    return len(rows), changed


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write rows (default is dry-run).",
    )
    parser.add_argument(
        "--user",
        action="append",
        help="Restrict to a single user. Repeatable. Defaults to hugo + andrea.",
    )
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", flush=True)
        return 1

    users = tuple(args.user) if args.user else DEFAULT_USERS
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] window={ZSCORE_WINDOW_DAYS}d rolling + 7d warmup; users={users}")

    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    total_examined = 0
    total_changed = 0
    try:
        async with session_factory() as session:
            for user_id in users:
                examined, changed = await _recompute_for_user(session, user_id, apply=args.apply)
                print(f"  user={user_id}: examined={examined} changed={changed}")
                total_examined += examined
                total_changed += changed
    finally:
        await engine.dispose()

    print(f"[{mode}] total examined={total_examined} changed={total_changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
