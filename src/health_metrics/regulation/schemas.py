"""Pydantic schemas for the v2 auto-regulation engine.

The shapes here are the canonical contract between compute_regulation() and
its consumers (the /api/v1/session-brief endpoint, the mcp-unified-server
get_session_brief tool, and the dashboard's daily card).
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class RegulationState(StrEnum):
    MAINTENANCE_SLEEP_DEFICIT = "MAINTENANCE_SLEEP_DEFICIT"
    MAINTENANCE_ILLNESS = "MAINTENANCE_ILLNESS"
    MAINTENANCE_PRE_PROCEDURE = "MAINTENANCE_PRE_PROCEDURE"
    MAINTENANCE_HRV_DEPRESSION = "MAINTENANCE_HRV_DEPRESSION"
    MAINTENANCE_LOW_RECOVERY = "MAINTENANCE_LOW_RECOVERY"
    DEFICIT_CONSERVATIVE = "DEFICIT_CONSERVATIVE"
    DEFICIT = "DEFICIT"


class TrainingModifier(StrEnum):
    REST = "REST"
    Z2_ONLY = "Z2_ONLY"
    VOLUME_MINUS_30_NO_HIIT = "VOLUME_MINUS_30_NO_HIIT"
    VOLUME_MINUS_20 = "VOLUME_MINUS_20"
    FULL_NO_PROGRESSION = "FULL_NO_PROGRESSION"
    FULL_PROGRESSION = "FULL_PROGRESSION"


class HealthEventSnapshot(BaseModel):
    """Health event projection used by compute_regulation(). Fewer fields than
    the DB row -- only what the engine needs."""

    event_type: Literal[
        "dental_procedure",
        "acute_infection",
        "antibiotic_course",
        "fever",
        "injury",
        "scheduled_lab_draw",
        "scheduled_dexa",
        "scheduled_sleep_study",
    ]
    status: Literal["active", "pending", "resolving", "resolved"]
    expected_resolution: date_type | None = None
    started_at: date_type | None = None


class DailySnapshot(BaseModel):
    """Pre-computed input to compute_regulation(). Pure function consumes this
    and emits a RegulationCall. The brief-builder (PR 3) constructs this from
    daily_metrics + workouts + health_events + trends queries."""

    user_id: str
    as_of_date: date_type

    # Sleep
    last_night_sleep_min: int | None = None
    sleep_3d_avg_min: float | None = None

    # Recovery
    recovery_today: int | None = None  # Whoop recovery_score 0-100
    hrv_z_3d: float | None = None
    consecutive_days_below_baseline: int = 0  # HRV z below baseline run

    # Strain
    strain_7d_mean: float = 0.0
    last_workout_max_hr_pct_age_predicted: float | None = None

    # Context
    active_events: list[HealthEventSnapshot] = Field(default_factory=list)
    history_days_count: int = 0  # for cold-start detection

    # Confidence inputs
    oura_present_today: bool = False
    whoop_present_today: bool = False
    subjective_logged_within_48h: bool = False


class AppliedOverride(BaseModel):
    """Records one manual override that modified the engine's RegulationCall
    (spec §13). Surfaced so the dashboard/chat can explain 'you're seeing 2500
    kcal instead of the engine's 2800 because <justification>'."""

    field: str
    from_value: str  # str() of the engine's original value
    to_value: str  # str() of the override value
    justification: str


class RegulationCall(BaseModel):
    """Output of compute_regulation(). What the brief surfaces."""

    state: RegulationState
    training_modifier: TrainingModifier
    kcal_target: int
    overrides_today: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    signals_considered: list[str] = Field(default_factory=list)
    applied_overrides: list[AppliedOverride] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]


class WorkoutSummary(BaseModel):
    """Projection of a recent workout for the brief -- subset of the workouts table."""

    workout_date: date_type
    workout_type: str | None = None
    duration_min: int | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    strain: float | None = None
    kcal: int | None = None
    max_hr_pct_age_predicted: float | None = None  # derived in the brief builder


class TrendSummary(BaseModel):
    """Short trend with current value + change vs window mean."""

    metric: str  # e.g., "hrv", "rhr", "sleep_min", "strain_7d"
    current: float | None = None
    window_mean: float | None = None
    delta: float | None = None  # current - window_mean


class WeightTrend(BaseModel):
    """Weight trajectory + revealed TDEE estimate."""

    n_days: int
    current_lbs: float | None = None  # raw most-recent reading; preserved
    delta_lbs: float | None = None  # raw endpoint delta; preserved for reference
    revealed_tdee_kcal: int | None = None  # NOW from filtered slope, not endpoints
    # New: Kalman-derived fields
    filtered_weight_lbs: float | None = None
    filtered_velocity_lbs_per_day: float | None = None
    revealed_tdee_confidence: Literal["high", "medium", "low"] | None = None
    # Training-water retention model (absolute water above fully-rested)
    training_water_offset_lbs: float | None = None  # today's ABSOLUTE training water (lb); ~1-2 lb after hard sessions
    weight_dewatered_lbs: float | None = None  # filtered weight − today's abs water; always set when workouts present
    weight_dewatered_7d_avg: float | None = None  # 7-day mean of the de-watered series; the decision variable
    training_water_clears_by: date_type | None = None  # date the kernel decays below 0.2 lb


class Flag(BaseModel):
    """User-visible alert from the brief."""

    code: str  # e.g., "watchpoint_hrv", "sleep_debt_high"
    severity: Literal["info", "watch", "alert"]
    message: str


class MissingInput(BaseModel):
    """What's missing from today's snapshot (drives confidence)."""

    field: str  # e.g., "oura_today", "whoop_today", "subjective_48h"
    impact: Literal["confidence_degrades", "engine_skipped_rule"]
    message: str | None = None


class SessionBrief(BaseModel):
    """Top-level brief returned by /api/v1/session-brief."""

    as_of_date: date_type
    user_id: str
    regulation_call: RegulationCall
    daily_snapshot: DailySnapshot
    recent_workouts: list[WorkoutSummary] = Field(default_factory=list)
    weight_trend: WeightTrend | None = None
    active_events: list[HealthEventSnapshot] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    missing_inputs: list[MissingInput] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]
    generated_at: datetime
