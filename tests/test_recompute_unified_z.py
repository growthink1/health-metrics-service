"""Smoke tests for scripts/recompute_unified_z.py.

The script is a thin orchestration layer over transforms/zscore.py (which is
already well-tested by test_recompute.py). These tests confirm:
1. The script's _baseline_values + _current_value helpers track the 14-day
   rolling window with 7-day warmup.
2. _as_decimal handles None correctly.
3. Dry-run mode does not write to the DB.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import text

# Load the script as a module without executing __main__.
SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "recompute_unified_z.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_recompute_unified_z", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_recompute_unified_z"] = mod
    spec.loader.exec_module(mod)
    return mod


script = _load_script()


class _FakeRow:
    """Lightweight stand-in for DailyMetrics with the columns the script reads."""

    def __init__(
        self,
        metric_date: date,
        oura_hrv_avg: float | None = None,
        whoop_hrv_ms: float | None = None,
        oura_rhr: float | None = None,
        whoop_rhr: float | None = None,
        oura_sleep_duration_min: float | None = None,
    ) -> None:
        self.metric_date = metric_date
        self.oura_hrv_avg = oura_hrv_avg
        self.whoop_hrv_ms = whoop_hrv_ms
        self.oura_rhr = oura_rhr
        self.whoop_rhr = whoop_rhr
        self.oura_sleep_duration_min = oura_sleep_duration_min


def test_baseline_window_is_14_days_strictly_before_target() -> None:
    """_baseline_values must include only rows in [target - 14, target)."""
    target = date(2026, 5, 20)
    rows: list[Any] = [
        _FakeRow(target - timedelta(days=15), oura_hrv_avg=42.0),  # out (too old)
        _FakeRow(target - timedelta(days=14), oura_hrv_avg=44.0),  # in
        _FakeRow(target - timedelta(days=7), oura_hrv_avg=46.0),  # in
        _FakeRow(target - timedelta(days=1), oura_hrv_avg=48.0),  # in
        _FakeRow(target, oura_hrv_avg=50.0),  # out (== target)
        _FakeRow(target + timedelta(days=1), oura_hrv_avg=52.0),  # out (after)
    ]
    vals = script._baseline_values(rows, target, "hrv")
    assert vals == [44.0, 46.0, 48.0]


def test_current_value_hrv_prefers_oura_falls_back_to_whoop() -> None:
    row = _FakeRow(date(2026, 5, 20), oura_hrv_avg=None, whoop_hrv_ms=44.5)
    assert script._current_value(row, "hrv") == 44.5
    row2 = _FakeRow(date(2026, 5, 20), oura_hrv_avg=42.0, whoop_hrv_ms=44.5)
    assert script._current_value(row2, "hrv") == 42.0
    row3 = _FakeRow(date(2026, 5, 20))
    assert script._current_value(row3, "hrv") is None


def test_current_value_unknown_selector_raises() -> None:
    row = _FakeRow(date(2026, 5, 20), oura_hrv_avg=42.0)
    with pytest.raises(ValueError):
        script._current_value(row, "bogus")


def test_as_decimal_quantizes_to_2dp() -> None:
    assert script._as_decimal(1.2345) == Decimal("1.23")
    assert script._as_decimal(None) is None


@pytest.mark.asyncio
async def test_dry_run_does_not_mutate(db_session, test_user_id) -> None:
    """End-to-end dry-run smoke against the real test DB. Seeds 10 days of
    data, runs _recompute_for_user(apply=False), confirms 0 rows mutated."""
    base = date(2026, 5, 1)
    for i in range(10):
        await db_session.execute(
            text(
                """
                INSERT INTO daily_metrics (user_id, metric_date,
                    oura_hrv_avg, oura_rhr, oura_sleep_duration_min,
                    unified_hrv_z, unified_rhr_z, unified_sleep_z,
                    oura_status, whoop_status, ingestion_complete)
                VALUES (:u, :d, :hrv, :rhr, :sleep, 0.0, 0.0, 0.0, 'ok', 'ok', TRUE)
                """
            ),
            {
                "u": test_user_id,
                "d": base + timedelta(days=i),
                "hrv": 40.0 + i,
                "rhr": 60.0 - i * 0.3,
                "sleep": 420.0 + i * 5,
            },
        )

    examined, changed = await script._recompute_for_user(
        db_session, test_user_id, apply=False
    )
    assert examined == 10
    # Some rows past the 7-day warmup will have new z-scores != stale 0.0.
    # Confirm dry-run reported changes but DB still has the original 0.0.
    assert changed > 0
    res = await db_session.execute(
        text(
            "SELECT unified_hrv_z FROM daily_metrics WHERE user_id=:u ORDER BY metric_date DESC LIMIT 1"
        ),
        {"u": test_user_id},
    )
    row_z = res.scalar()
    # Dry-run must not have written -- original stale value (0.00) remains.
    assert row_z == Decimal("0.00")
