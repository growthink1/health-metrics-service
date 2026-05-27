"""Auto-regulation decision rules.

Mirrors the deterministic decision tree from docs/spec.md §"Auto-regulation
logic". Conservative bias — when ambiguous, prefer the safer recommendation.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DailyMetrics, ManualLog


@dataclass
class RegulationSignals:
    hrv_z_3d: float                       # avg z-score over last 3 days vs 14d baseline
    rhr_z_3d: float
    sleep_3d_min: float | None            # avg minutes over last 3 days; None if no sleep data
    sleep_debt_min: float                 # from Whoop (positive = behind)
    strain_7d_total: float
    subjective_3d_energy: float | None    # 1-10, None if missing
    days_with_complete_data: int          # of last 3, how many ingestion_complete


RecType = Literal["deficit", "deficit_conservative", "maintenance", "deload"]


def regulate(s: RegulationSignals) -> tuple[RecType, list[str], dict]:
    """Return (recommendation, rationale_list, action_payload)."""
    rationale: list[str] = []

    # Sleep data unavailable (Oura outage, charging strap, ingestion gap, etc.):
    # do NOT silently treat as zero — the < 300 floor would otherwise false-positive
    # DELOAD on every missing-data day. Default to maintenance with a clear rationale.
    if s.sleep_3d_min is None:
        rationale.append("Sleep data unavailable — using conservative default")
        return (
            "maintenance",
            rationale,
            {"kcal": 2800, "training": "Full program, no progression push"},
        )

    recovery_score = (
        -0.50 * s.hrv_z_3d
        - 0.30 * s.rhr_z_3d
        + 0.40 * ((s.sleep_3d_min - 360) / 60)
    )

    # Hard floor: severe sleep deprivation
    if s.sleep_3d_min < 300:
        rationale.append(f"Severe sleep debt: {s.sleep_3d_min / 60:.1f}h avg over 3d")
        return (
            "deload",
            rationale,
            {"kcal": 2800, "training": "Volume -30%, Z2 only, extra rest day"},
        )

    # Hard floor: subjective collapse (if logged)
    if s.subjective_3d_energy is not None and s.subjective_3d_energy < 4:
        rationale.append(
            f"Subjective energy collapsed: {s.subjective_3d_energy:.1f}/10"
        )
        return (
            "deload",
            rationale,
            {"kcal": 2800, "training": "Volume -30%, Z2 only, extra rest day"},
        )

    # Severe recovery + sleep compromise
    if recovery_score < -1.0 and s.sleep_3d_min < 360:
        rationale.append(
            f"Recovery composite {recovery_score:.2f} + sleep {s.sleep_3d_min / 60:.1f}h"
        )
        return (
            "deload",
            rationale,
            {"kcal": 2800, "training": "Volume -30%, swap HIIT for Z2"},
        )

    # Mild recovery compromise — pause deficit, train normally
    if recovery_score < -0.5 or s.sleep_3d_min < 390:
        rationale.append(
            f"Recovery markers depressed (score {recovery_score:.2f}, "
            f"sleep {s.sleep_3d_min / 60:.1f}h)"
        )
        return (
            "maintenance",
            rationale,
            {"kcal": 2800, "training": "Full program, no progression push"},
        )

    # Excessive strain accumulation
    if s.strain_7d_total / 7 > 15:
        rationale.append(
            f"7d strain load high: {s.strain_7d_total:.1f} "
            f"({s.strain_7d_total / 7:.1f}/day avg)"
        )
        return (
            "deficit_conservative",
            rationale,
            {"kcal": 2500, "training": "Full program, monitor closely"},
        )

    # All clear
    if recovery_score > 0 and s.strain_7d_total / 7 < 13:
        rationale.append(
            f"All signals green: recovery {recovery_score:.2f}, "
            f"strain {s.strain_7d_total / 7:.1f}/d"
        )
        return (
            "deficit",
            rationale,
            {"kcal": 2300, "training": "Full program, progression OK"},
        )

    # Conservative bias default
    rationale.append(
        f"Mixed signals (recovery {recovery_score:.2f}, "
        f"strain {s.strain_7d_total / 7:.1f}/d) — conservative"
    )
    return (
        "deficit_conservative",
        rationale,
        {"kcal": 2500, "training": "Full program, monitor closely"},
    )


async def compute_regulation_signals(
    session: AsyncSession, user_id: str, anchor: date
) -> RegulationSignals:
    """Pull last-3-day daily_metrics + manual_log, aggregate into RegulationSignals."""
    window_start = anchor - timedelta(days=2)  # anchor + 2 prior = 3 days

    # daily_metrics for last 3 days
    res = await session.execute(
        select(DailyMetrics)
        .where(DailyMetrics.user_id == user_id)
        .where(DailyMetrics.metric_date >= window_start)
        .where(DailyMetrics.metric_date <= anchor)
        .order_by(DailyMetrics.metric_date.asc())
    )
    dm_rows = list(res.scalars().all())

    # manual_log for last 3 days
    res = await session.execute(
        select(ManualLog)
        .where(ManualLog.user_id == user_id)
        .where(ManualLog.log_date >= window_start)
        .where(ManualLog.log_date <= anchor)
        .order_by(ManualLog.log_date.asc())
    )
    ml_rows = list(res.scalars().all())

    # 7-day strain window
    strain_start = anchor - timedelta(days=6)
    res = await session.execute(
        select(DailyMetrics)
        .where(DailyMetrics.user_id == user_id)
        .where(DailyMetrics.metric_date >= strain_start)
        .where(DailyMetrics.metric_date <= anchor)
    )
    strain_rows = list(res.scalars().all())

    def _avg(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    hrv_zs = [float(r.unified_hrv_z) for r in dm_rows if r.unified_hrv_z is not None]
    rhr_zs = [float(r.unified_rhr_z) for r in dm_rows if r.unified_rhr_z is not None]
    sleep_mins = [
        float(r.oura_sleep_duration_min)
        for r in dm_rows
        if r.oura_sleep_duration_min is not None
    ]
    sleep_debts = [
        float(r.whoop_sleep_debt_min)
        for r in dm_rows
        if r.whoop_sleep_debt_min is not None
    ]
    strain_total = sum(
        float(r.whoop_day_strain) for r in strain_rows if r.whoop_day_strain is not None
    )

    energies = [
        float(r.subjective_energy)
        for r in ml_rows
        if r.subjective_energy is not None
    ]

    return RegulationSignals(
        hrv_z_3d=_avg(hrv_zs),
        rhr_z_3d=_avg(rhr_zs),
        # Missing-data is None, NOT 0.0 — the regulate() short-circuit relies on this
        # distinction to avoid the false-positive < 300 'severe sleep' DELOAD.
        sleep_3d_min=_avg(sleep_mins) if sleep_mins else None,
        sleep_debt_min=_avg(sleep_debts) if sleep_debts else 0.0,
        strain_7d_total=strain_total,
        subjective_3d_energy=_avg(energies) if energies else None,
        days_with_complete_data=sum(1 for r in dm_rows if r.ingestion_complete),
    )
