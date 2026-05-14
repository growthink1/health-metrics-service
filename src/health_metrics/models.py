"""SQLAlchemy ORM models — mirrors docs/spec.md §3 schema."""

from datetime import date as date_type, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class DailyMetrics(Base):
    __tablename__ = "daily_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    metric_date: Mapped[date_type] = mapped_column(Date, nullable=False)

    # Oura
    oura_sleep_score: Mapped[Optional[int]] = mapped_column(Integer)
    oura_sleep_duration_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_sleep_efficiency: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    oura_sleep_latency_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_rem_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_deep_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_light_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_awake_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_hrv_avg: Mapped[Optional[int]] = mapped_column(Integer)
    oura_rhr: Mapped[Optional[int]] = mapped_column(Integer)
    oura_temp_deviation: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2))
    oura_readiness_score: Mapped[Optional[int]] = mapped_column(Integer)
    oura_raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    # Whoop
    whoop_recovery_score: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_hrv_ms: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    whoop_rhr: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_sleep_performance: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_sleep_need_min: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_sleep_debt_min: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_day_strain: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2))
    whoop_avg_hr: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_max_hr: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_kcal_burned: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    # Derived
    unified_hrv_z: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    unified_rhr_z: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    unified_sleep_z: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    ingestion_complete: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    oura_status: Mapped[Optional[str]] = mapped_column(Text)
    whoop_status: Mapped[Optional[str]] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        UniqueConstraint("user_id", "metric_date", name="uq_daily_metrics_user_date"),
        Index("idx_daily_metrics_user_date", "user_id", "metric_date"),
    )


class Workout(Base):
    __tablename__ = "workouts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    workout_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    workout_type: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_hr: Mapped[Optional[int]] = mapped_column(Integer)
    max_hr: Mapped[Optional[int]] = mapped_column(Integer)
    strain: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2))
    kcal: Mapped[Optional[int]] = mapped_column(Integer)
    zone_0_min: Mapped[Optional[int]] = mapped_column(Integer)
    zone_1_min: Mapped[Optional[int]] = mapped_column(Integer)
    zone_2_min: Mapped[Optional[int]] = mapped_column(Integer)
    zone_3_min: Mapped[Optional[int]] = mapped_column(Integer)
    zone_4_min: Mapped[Optional[int]] = mapped_column(Integer)
    zone_5_min: Mapped[Optional[int]] = mapped_column(Integer)
    raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_workouts_source_sourceid"),
        Index("idx_workouts_user_date", "user_id", "workout_date"),
        Index("idx_workouts_type", "workout_type"),
    )


class ManualLog(Base):
    __tablename__ = "manual_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    log_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    weight_lbs: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    kcal_consumed: Mapped[Optional[int]] = mapped_column(Integer)
    protein_g: Mapped[Optional[int]] = mapped_column(Integer)
    fat_g: Mapped[Optional[int]] = mapped_column(Integer)
    carbs_g: Mapped[Optional[int]] = mapped_column(Integer)
    subjective_energy: Mapped[Optional[int]] = mapped_column(Integer)
    subjective_mood: Mapped[Optional[int]] = mapped_column(Integer)
    subjective_hunger: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        UniqueConstraint("user_id", "log_date", name="uq_manual_log_user_date"),
    )


class RegulationRecommendation(Base):
    __tablename__ = "regulation_recommendations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    rec_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    rec_type: Mapped[Optional[str]] = mapped_column(Text)
    suggested_kcal: Mapped[Optional[int]] = mapped_column(Integer)
    suggested_training_mod: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[str]] = mapped_column(Text)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    triggering_signals: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_reg_rec_user_date", "user_id", "rec_date"),
    )


class OAuthState(Base):
    """Persists rotating refresh tokens for Whoop OAuth (Gotcha #3)."""

    __tablename__ = "oauth_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    access_token: Mapped[Optional[str]] = mapped_column(Text)
    access_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        UniqueConstraint("provider", "user_id", name="uq_oauth_state_provider_user"),
    )


class NarrationCache(Base):
    """Content-addressed cache of Claude-generated narration sentences.

    Keyed on (user_id, metric_date, signals_hash). The signals_hash is
    SHA256 of the canonical JSON of the regulation signals payload — so
    if signals don't change, the cached narration is reused indefinitely.
    """

    __tablename__ = "narration_cache"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    metric_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    signals_hash: Mapped[str] = mapped_column(Text, nullable=False)
    narration_text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "metric_date", "signals_hash",
            name="uq_narration_cache_user_date_hash",
        ),
    )
