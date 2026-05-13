"""Build daily_metrics row dict from source payloads."""

from datetime import date
from typing import Any

from ..sources.base import OuraDayPayload, WhoopDayPayload


def build_daily_metrics_row(
    user_id: str,
    oura: OuraDayPayload | None,
    whoop: WhoopDayPayload | None,
    oura_status: str,
    whoop_status: str,
) -> dict[str, Any]:
    """Produce a dict matching the DailyMetrics ORM column names."""
    metric_date: date = (oura.metric_date if oura else whoop.metric_date)  # type: ignore[union-attr]

    row: dict[str, Any] = {
        "user_id": user_id,
        "metric_date": metric_date,
        # Oura
        "oura_sleep_score": _g(oura, "sleep_score"),
        "oura_sleep_duration_min": _g(oura, "sleep_duration_min"),
        "oura_sleep_efficiency": _g(oura, "sleep_efficiency"),
        "oura_sleep_latency_min": _g(oura, "sleep_latency_min"),
        "oura_rem_min": _g(oura, "rem_min"),
        "oura_deep_min": _g(oura, "deep_min"),
        "oura_light_min": _g(oura, "light_min"),
        "oura_awake_min": _g(oura, "awake_min"),
        "oura_hrv_avg": _g(oura, "hrv_avg"),
        "oura_rhr": _g(oura, "rhr"),
        "oura_temp_deviation": _g(oura, "temp_deviation"),
        "oura_readiness_score": _g(oura, "readiness_score"),
        "oura_raw": _g(oura, "raw"),
        # Whoop
        "whoop_recovery_score": _g(whoop, "recovery_score"),
        "whoop_hrv_ms": _g(whoop, "hrv_ms"),
        "whoop_rhr": _g(whoop, "rhr"),
        "whoop_sleep_performance": _g(whoop, "sleep_performance"),
        "whoop_sleep_need_min": _g(whoop, "sleep_need_min"),
        "whoop_sleep_debt_min": _g(whoop, "sleep_debt_min"),
        "whoop_day_strain": _g(whoop, "day_strain"),
        "whoop_avg_hr": _g(whoop, "avg_hr"),
        "whoop_max_hr": _g(whoop, "max_hr"),
        "whoop_kcal_burned": _g(whoop, "kcal_burned"),
        "whoop_raw": _g(whoop, "raw"),
        # Status
        "oura_status": oura_status,
        "whoop_status": whoop_status,
        "ingestion_complete": oura_status == "ok" and whoop_status == "ok",
    }
    return row


def _g(obj: Any, attr: str) -> Any:
    if obj is None:
        return None
    return getattr(obj, attr, None)
