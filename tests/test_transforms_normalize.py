from datetime import date

from health_metrics.sources.base import OuraDayPayload, WhoopDayPayload
from health_metrics.transforms.normalize import build_daily_metrics_row


def test_build_daily_metrics_row_merges_both_sources():
    oura = OuraDayPayload(
        metric_date=date(2026, 5, 12),
        sleep_score=78,
        sleep_duration_min=412,
        sleep_efficiency=89.2,
        hrv_avg=45,
        rhr=58,
        readiness_score=72,
        raw={"k": "v"},
    )
    whoop = WhoopDayPayload(
        metric_date=date(2026, 5, 12),
        recovery_score=65,
        hrv_ms=42.5,
        rhr=60,
        day_strain=14.2,
        kcal_burned=2342,
        raw={"k2": "v2"},
    )

    row = build_daily_metrics_row(
        user_id="hugo",
        oura=oura,
        whoop=whoop,
        oura_status="ok",
        whoop_status="ok",
    )

    assert row["user_id"] == "hugo"
    assert row["metric_date"] == date(2026, 5, 12)
    assert row["oura_sleep_score"] == 78
    assert row["oura_hrv_avg"] == 45
    assert row["whoop_recovery_score"] == 65
    assert row["whoop_hrv_ms"] == 42.5
    assert row["whoop_day_strain"] == 14.2
    assert row["whoop_kcal_burned"] == 2342
    assert row["ingestion_complete"] is True
    assert row["oura_status"] == "ok"
    assert row["whoop_status"] == "ok"


def test_build_daily_metrics_row_partial_oura_only():
    oura = OuraDayPayload(metric_date=date(2026, 5, 12), sleep_score=78)
    row = build_daily_metrics_row(
        user_id="hugo",
        oura=oura,
        whoop=None,
        oura_status="ok",
        whoop_status="failed",
    )
    assert row["oura_sleep_score"] == 78
    assert row["whoop_recovery_score"] is None
    assert row["ingestion_complete"] is False
