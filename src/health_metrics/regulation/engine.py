"""Pure-function regulation engine. No I/O. Single source of truth for the
RegulationCall (spec Invariant #1)."""

from datetime import date as date_type
from datetime import timedelta
from typing import Literal

from .schemas import (
    DailySnapshot,
    HealthEventSnapshot,
    RegulationCall,
    RegulationState,
    TrainingModifier,
)

# Per-user kcal targets per spec §4.1.
_KCAL_TARGETS: dict[str, dict[RegulationState, int]] = {
    "hugo": {
        RegulationState.MAINTENANCE_SLEEP_DEFICIT: 2800,
        RegulationState.MAINTENANCE_ILLNESS: 2800,
        RegulationState.MAINTENANCE_PRE_PROCEDURE: 2800,
        RegulationState.MAINTENANCE_HRV_DEPRESSION: 2800,
        RegulationState.MAINTENANCE_LOW_RECOVERY: 2800,
        RegulationState.DEFICIT_CONSERVATIVE: 2500,
        RegulationState.DEFICIT: 2300,
    },
    "andrea": {
        RegulationState.MAINTENANCE_SLEEP_DEFICIT: 2400,
        RegulationState.MAINTENANCE_ILLNESS: 2400,
        RegulationState.MAINTENANCE_PRE_PROCEDURE: 2400,
        RegulationState.MAINTENANCE_HRV_DEPRESSION: 2400,
        RegulationState.MAINTENANCE_LOW_RECOVERY: 2400,
        RegulationState.DEFICIT_CONSERVATIVE: 2150,
        RegulationState.DEFICIT: 2000,
    },
}


def _has_active_event(events: list[HealthEventSnapshot], et: str) -> HealthEventSnapshot | None:
    for e in events:
        if e.event_type == et and e.status == "active":
            return e
    return None


def _has_pending_within(
    events: list[HealthEventSnapshot],
    et: str,
    anchor: date_type,
    days: int,
) -> HealthEventSnapshot | None:
    cutoff = anchor + timedelta(days=days)
    for e in events:
        if (
            e.event_type == et
            and e.status == "pending"
            and e.expected_resolution is not None
            and anchor <= e.expected_resolution <= cutoff
        ):
            return e
    return None


def _post_procedure_within(
    events: list[HealthEventSnapshot],
    et: str,
    anchor: date_type,
    days: int,
) -> HealthEventSnapshot | None:
    """A resolving/resolved procedure within the last `days` days."""
    earliest = anchor - timedelta(days=days)
    for e in events:
        if (
            e.event_type == et
            and e.status in ("resolving", "resolved")
            and e.started_at is not None
            and earliest <= e.started_at <= anchor
        ):
            return e
    return None


def _confidence(snap: DailySnapshot) -> Literal["high", "medium", "low"]:
    missing = 0
    if not snap.oura_present_today:
        missing += 1
    if not snap.whoop_present_today:
        missing += 1
    if snap.history_days_count < 14:
        missing += 1
    if not snap.subjective_logged_within_48h:
        missing += 1
    if missing == 0:
        return "high"
    if missing == 1:
        return "medium"
    return "low"


def compute_regulation(snap: DailySnapshot) -> RegulationCall:
    """Per spec §4.1 -- priority order, first match wins.
    Then §4.2 -- overrides are additive on top of the chosen state."""
    rationale: list[str] = []
    overrides: list[str] = []
    state: RegulationState
    tmod: TrainingModifier

    # Decision tree (priority order)
    # Priority 1: sleep deficit
    if (snap.last_night_sleep_min is not None and snap.last_night_sleep_min < 240) or (
        snap.sleep_3d_avg_min is not None and snap.sleep_3d_avg_min < 300
    ):
        state = RegulationState.MAINTENANCE_SLEEP_DEFICIT
        tmod = TrainingModifier.Z2_ONLY
        rationale.append(
            f"Sleep deficit (last night {snap.last_night_sleep_min} min, 3d avg {snap.sleep_3d_avg_min} min)"
        )
        overrides.append("no_lift_today")
    # Priority 2: active acute infection
    elif _has_active_event(snap.active_events, "acute_infection"):
        state = RegulationState.MAINTENANCE_ILLNESS
        tmod = TrainingModifier.REST
        rationale.append("Active acute infection -- full rest")
        overrides.append("no_training")
    # Priority 3: pending dental procedure within 14d
    elif dent := _has_pending_within(snap.active_events, "dental_procedure", snap.as_of_date, 14):
        state = RegulationState.MAINTENANCE_PRE_PROCEDURE
        if snap.recovery_today is not None and snap.recovery_today >= 60:
            tmod = TrainingModifier.VOLUME_MINUS_20
        else:
            tmod = TrainingModifier.Z2_ONLY
        rationale.append(
            f"Pending dental procedure {dent.expected_resolution.isoformat() if dent.expected_resolution else 'soon'}"
        )
        overrides.extend(
            [
                "no_deficit_pre_procedure",
                "no_z4_plus",
                "rpe_cap_7",
                "watch_jaw_load",
            ]
        )
    # Priority 4: HRV depression
    elif snap.hrv_z_3d is not None and snap.hrv_z_3d < -1.0 and snap.consecutive_days_below_baseline >= 3:
        state = RegulationState.MAINTENANCE_HRV_DEPRESSION
        tmod = TrainingModifier.VOLUME_MINUS_30_NO_HIIT
        rationale.append(
            f"HRV depression: z3 {snap.hrv_z_3d:.2f} for {snap.consecutive_days_below_baseline} consecutive days"
        )
    # Priority 4.5a: severe low recovery
    elif snap.recovery_today is not None and snap.recovery_today < 33:
        state = RegulationState.MAINTENANCE_LOW_RECOVERY
        tmod = TrainingModifier.VOLUME_MINUS_20
        rationale.append(f"Low recovery: Whoop recovery {snap.recovery_today} (< 33)")
    # Priority 4.5b: depressed recovery
    elif snap.recovery_today is not None and snap.recovery_today < 40:
        state = RegulationState.DEFICIT_CONSERVATIVE
        tmod = TrainingModifier.FULL_NO_PROGRESSION
        rationale.append(f"Depressed recovery: Whoop recovery {snap.recovery_today} (< 40)")
    # Priority 5: high strain + high recovery
    elif snap.strain_7d_mean > 12 and snap.recovery_today is not None and snap.recovery_today > 70:
        state = RegulationState.DEFICIT_CONSERVATIVE
        tmod = TrainingModifier.FULL_NO_PROGRESSION
        rationale.append(f"High strain load (7d mean {snap.strain_7d_mean:.1f}) with recovery {snap.recovery_today}")
    # Priority 6: cold start
    elif snap.history_days_count < 14:
        state = RegulationState.DEFICIT_CONSERVATIVE
        tmod = TrainingModifier.FULL_NO_PROGRESSION
        rationale.append(f"Cold start ({snap.history_days_count} days history)")
    # Priority 7: default (all green)
    else:
        state = RegulationState.DEFICIT
        tmod = TrainingModifier.FULL_PROGRESSION
        rationale.append("All signals green")

    # Spec §4.2 -- additive overrides (run regardless of state)
    # Override 1: pending dental within 14d (skip if already added above)
    if "no_deficit_pre_procedure" not in overrides and _has_pending_within(
        snap.active_events, "dental_procedure", snap.as_of_date, 14
    ):
        overrides.extend(
            [
                "no_deficit_pre_procedure",
                "no_z4_plus",
                "rpe_cap_7",
                "watch_jaw_load",
            ]
        )

    # Override 2: yesterday max HR >= 0.95 age-predicted -> no_z4_plus today
    if (
        snap.last_workout_max_hr_pct_age_predicted is not None
        and snap.last_workout_max_hr_pct_age_predicted >= 0.95
        and "no_z4_plus" not in overrides
    ):
        overrides.append("no_z4_plus")

    # Override 3: antibiotic course active
    if _has_active_event(snap.active_events, "antibiotic_course"):
        overrides.extend(["monitor_gi", "hydration_plus"])

    # Override 4: post-extraction soft-food bridge (within 7d)
    if _post_procedure_within(snap.active_events, "dental_procedure", snap.as_of_date, 7):
        overrides.extend(["soft_food_only", "no_overhead_press"])

    # Override 5: HRV z below -0.5 across 2 days (not yet 3 -- that's already P4)
    if snap.hrv_z_3d is not None and snap.hrv_z_3d < -0.5 and snap.consecutive_days_below_baseline == 2:
        overrides.append("watchpoint_hrv")

    # Honest rationale: never claim "all green" when overrides fired.
    if overrides and "All signals green" in rationale:
        rationale = [r for r in rationale if r != "All signals green"]
        rationale.append("Watchpoints active: " + ", ".join(overrides))

    # Decision B: auditable record of the key inputs the engine evaluated.
    signals_considered = [
        f"recovery_today={snap.recovery_today}",
        f"hrv_z_3d={snap.hrv_z_3d}",
        f"consecutive_days_below_baseline={snap.consecutive_days_below_baseline}",
        f"strain_7d_mean={snap.strain_7d_mean}",
        f"last_night_sleep_min={snap.last_night_sleep_min}",
        f"history_days_count={snap.history_days_count}",
    ]

    kcal = _KCAL_TARGETS.get(snap.user_id, _KCAL_TARGETS["hugo"])[state]

    return RegulationCall(
        state=state,
        training_modifier=tmod,
        kcal_target=kcal,
        overrides_today=overrides,
        rationale=rationale,
        signals_considered=signals_considered,
        confidence=_confidence(snap),
    )
