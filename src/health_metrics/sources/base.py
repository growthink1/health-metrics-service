"""Common types for source clients."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class OuraDayPayload:
    """Normalized Oura payload for one date."""

    metric_date: date
    sleep_score: int | None = None
    sleep_duration_min: int | None = None
    sleep_efficiency: float | None = None
    sleep_latency_min: int | None = None
    rem_min: int | None = None
    deep_min: int | None = None
    light_min: int | None = None
    awake_min: int | None = None
    hrv_avg: int | None = None
    rhr: int | None = None
    temp_deviation: float | None = None
    readiness_score: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WhoopDayPayload:
    """Normalized Whoop payload for one date."""

    metric_date: date
    recovery_score: int | None = None
    hrv_ms: float | None = None
    rhr: int | None = None
    sleep_performance: int | None = None
    sleep_need_min: int | None = None
    sleep_debt_min: int | None = None
    day_strain: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    kcal_burned: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WhoopWorkout:
    source_id: str
    workout_date: date
    workout_type: str | None
    started_at: str
    duration_min: int
    avg_hr: int | None
    max_hr: int | None
    strain: float | None
    kcal: int | None
    zone_minutes: dict[int, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
