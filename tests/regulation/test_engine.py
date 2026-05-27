"""Fixture-driven tests for compute_regulation.

100% branch coverage on engine.py -- every decision-tree branch + every
override path + every confidence permutation is exercised.
"""

import json
from pathlib import Path

import pytest

from health_metrics.regulation.engine import compute_regulation
from health_metrics.regulation.schemas import DailySnapshot, HealthEventSnapshot

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "days"


def _load(name: str) -> tuple[DailySnapshot, dict]:
    data = json.loads((FIXTURE_DIR / name).read_text())
    return DailySnapshot(**data["snapshot"]), data["expected"]


@pytest.mark.parametrize(
    "fixture_name,expected_state",
    [
        ("2026-05-17-infection-peak.json", "MAINTENANCE_ILLNESS"),
        ("2026-05-24-sleep-crash.json", "MAINTENANCE_SLEEP_DEFICIT"),
        ("2026-05-26-pre-extraction.json", "MAINTENANCE_PRE_PROCEDURE"),
        ("2026-05-25-post-z5-spike.json", "MAINTENANCE_PRE_PROCEDURE"),
        ("green-baseline-hugo.json", "DEFICIT"),
        ("cold-start-andrea.json", "DEFICIT_CONSERVATIVE"),
        ("hrv-depression-3d.json", "MAINTENANCE_HRV_DEPRESSION"),
    ],
)
def test_fixture_produces_expected_state(fixture_name: str, expected_state: str) -> None:
    snap, expected = _load(fixture_name)
    call = compute_regulation(snap)
    assert call.state.value == expected_state
    for ov in expected.get("overrides_today_superset", []):
        assert ov in call.overrides_today, f"{fixture_name}: expected override {ov!r} in {call.overrides_today}"
    if "confidence_at_most" in expected:
        levels = {"high": 3, "medium": 2, "low": 1}
        assert levels[call.confidence] <= levels[expected["confidence_at_most"]]


def test_kcal_target_hugo_deficit() -> None:
    """Sanity: Hugo's DEFICIT state maps to 2300 kcal."""
    snap, _ = _load("green-baseline-hugo.json")
    call = compute_regulation(snap)
    assert call.kcal_target == 2300


def test_kcal_target_andrea_cold_start() -> None:
    snap, _ = _load("cold-start-andrea.json")
    call = compute_regulation(snap)
    assert call.kcal_target == 2150  # DEFICIT_CONSERVATIVE for Andrea


def test_kcal_target_unknown_user_falls_back_to_hugo() -> None:
    """Engine should not KeyError on an unknown user_id."""
    snap, _ = _load("green-baseline-hugo.json")
    snap = snap.model_copy(update={"user_id": "stranger"})
    call = compute_regulation(snap)
    assert call.kcal_target == 2300  # hugo's DEFICIT


def test_confidence_high_when_all_inputs_present() -> None:
    snap, _ = _load("green-baseline-hugo.json")
    call = compute_regulation(snap)
    assert call.confidence == "high"


def test_confidence_medium_when_one_input_missing() -> None:
    snap, _ = _load("green-baseline-hugo.json")
    snap = snap.model_copy(update={"oura_present_today": False})
    call = compute_regulation(snap)
    assert call.confidence == "medium"


def test_confidence_low_when_two_inputs_missing() -> None:
    """Drop Oura + history days -> 2 missing -> low."""
    snap, _ = _load("green-baseline-hugo.json")
    snap = snap.model_copy(update={"oura_present_today": False, "history_days_count": 5})
    call = compute_regulation(snap)
    assert call.confidence == "low"


def test_post_extraction_adds_soft_food_override() -> None:
    """Spec §4.2 override 4: post-extraction soft-food bridge."""
    snap, _ = _load("green-baseline-hugo.json")
    snap = snap.model_copy(
        update={
            "active_events": [
                HealthEventSnapshot(
                    event_type="dental_procedure",
                    status="resolved",
                    started_at=snap.as_of_date,
                )
            ]
        }
    )
    call = compute_regulation(snap)
    assert "soft_food_only" in call.overrides_today
    assert "no_overhead_press" in call.overrides_today


def test_antibiotic_course_adds_gi_monitoring() -> None:
    snap, _ = _load("green-baseline-hugo.json")
    snap = snap.model_copy(
        update={
            "active_events": [
                HealthEventSnapshot(
                    event_type="antibiotic_course",
                    status="active",
                    started_at=snap.as_of_date,
                )
            ]
        }
    )
    call = compute_regulation(snap)
    assert "monitor_gi" in call.overrides_today
    assert "hydration_plus" in call.overrides_today


def test_watchpoint_hrv_at_two_consecutive_days() -> None:
    """Spec §4.2 override 5: HRV z below -0.5 across 2 days but not yet 3."""
    snap, _ = _load("green-baseline-hugo.json")
    snap = snap.model_copy(update={"hrv_z_3d": -0.6, "consecutive_days_below_baseline": 2})
    call = compute_regulation(snap)
    assert "watchpoint_hrv" in call.overrides_today


def test_no_z4_plus_from_yesterday_max_hr_spike() -> None:
    """Spec §4.2 override 2: max HR >= 0.95 age-predicted yesterday triggers
    no_z4_plus today, even without a pending procedure adding it."""
    snap, _ = _load("green-baseline-hugo.json")
    snap = snap.model_copy(update={"last_workout_max_hr_pct_age_predicted": 0.97})
    call = compute_regulation(snap)
    assert "no_z4_plus" in call.overrides_today


def test_pre_procedure_low_recovery_drops_to_z2_only() -> None:
    """Spec §4.1 priority 3: when recovery < 60 the pre-procedure modifier
    falls from VOLUME_MINUS_20 to Z2_ONLY."""
    snap, _ = _load("2026-05-26-pre-extraction.json")
    snap = snap.model_copy(update={"recovery_today": 45})
    call = compute_regulation(snap)
    assert call.training_modifier.value == "Z2_ONLY"


def test_high_strain_high_recovery_triggers_deficit_conservative() -> None:
    """Spec §4.1 priority 5: strain_7d_mean > 12 AND recovery > 70."""
    snap, _ = _load("green-baseline-hugo.json")
    snap = snap.model_copy(update={"strain_7d_mean": 14.0, "recovery_today": 80})
    call = compute_regulation(snap)
    assert call.state.value == "DEFICIT_CONSERVATIVE"
    assert call.training_modifier.value == "FULL_NO_PROGRESSION"


def test_sleep_deficit_via_last_night_only() -> None:
    """Priority 1 branch: last_night_sleep < 240 even if 3d avg is fine."""
    snap, _ = _load("green-baseline-hugo.json")
    snap = snap.model_copy(update={"last_night_sleep_min": 220, "sleep_3d_avg_min": 450.0})
    call = compute_regulation(snap)
    assert call.state.value == "MAINTENANCE_SLEEP_DEFICIT"


def test_rationale_is_populated() -> None:
    """Every call should emit at least one rationale entry."""
    snap, _ = _load("green-baseline-hugo.json")
    call = compute_regulation(snap)
    assert len(call.rationale) >= 1


def test_confidence_counts_whoop_missing_branch() -> None:
    """Cover the whoop-missing increment specifically (separate from Oura branch)."""
    snap, _ = _load("green-baseline-hugo.json")
    snap = snap.model_copy(update={"whoop_present_today": False})
    call = compute_regulation(snap)
    # 1 input missing -> medium
    assert call.confidence == "medium"


def test_pending_dental_override_added_when_state_is_sleep_deficit() -> None:
    """Override 1 (pending dental procedure adds no_deficit_pre_procedure et al)
    must fire even when the engine routes to a higher-priority state. Sleep
    deficit (priority 1) wins the state, but the override branch still appends
    the dental overrides to overrides_today."""
    snap, _ = _load("green-baseline-hugo.json")
    from datetime import timedelta

    snap = snap.model_copy(
        update={
            "last_night_sleep_min": 220,  # triggers priority 1 sleep deficit
            "sleep_3d_avg_min": 280.0,
            "active_events": [
                HealthEventSnapshot(
                    event_type="dental_procedure",
                    status="pending",
                    expected_resolution=snap.as_of_date + timedelta(days=10),
                    started_at=snap.as_of_date - timedelta(days=2),
                )
            ],
        }
    )
    call = compute_regulation(snap)
    assert call.state.value == "MAINTENANCE_SLEEP_DEFICIT"
    # Override path 1 must have appended these on top of the sleep-deficit state
    assert "no_deficit_pre_procedure" in call.overrides_today
    assert "no_z4_plus" in call.overrides_today
    assert "rpe_cap_7" in call.overrides_today
    assert "watch_jaw_load" in call.overrides_today
    # Also retains the sleep-deficit override
    assert "no_lift_today" in call.overrides_today
