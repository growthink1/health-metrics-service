"""Pydantic schemas for the v2 auto-regulation engine.

The shapes here are the canonical contract between compute_regulation() and
its consumers (the /api/v1/session-brief endpoint, the mcp-unified-server
get_session_brief tool, and the dashboard's daily card).
"""

from __future__ import annotations

from datetime import date as date_type
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class RegulationState(StrEnum):
    MAINTENANCE_SLEEP_DEFICIT = "MAINTENANCE_SLEEP_DEFICIT"
    MAINTENANCE_ILLNESS = "MAINTENANCE_ILLNESS"
    MAINTENANCE_PRE_PROCEDURE = "MAINTENANCE_PRE_PROCEDURE"
    MAINTENANCE_HRV_DEPRESSION = "MAINTENANCE_HRV_DEPRESSION"
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


class RegulationCall(BaseModel):
    """Output of compute_regulation(). What the brief surfaces."""

    state: RegulationState
    training_modifier: TrainingModifier
    kcal_target: int
    overrides_today: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]
