from datetime import date

import pytest
from sqlalchemy import text

from health_metrics.regulation import RegulationSignals, regulate, compute_regulation_signals


def test_severe_sleep_deprivation_triggers_deload():
    s = RegulationSignals(
        hrv_z_3d=0.0, rhr_z_3d=0.0, sleep_3d_min=280,
        sleep_debt_min=600, strain_7d_total=70,
        subjective_3d_energy=6, days_with_complete_data=3,
    )
    rec, rationale, payload = regulate(s)
    assert rec == "deload"
    assert payload["kcal"] == 2800


def test_subjective_energy_collapse_triggers_deload():
    s = RegulationSignals(
        hrv_z_3d=0.0, rhr_z_3d=0.0, sleep_3d_min=420,
        sleep_debt_min=120, strain_7d_total=70,
        subjective_3d_energy=3.5, days_with_complete_data=3,
    )
    rec, _, _ = regulate(s)
    assert rec == "deload"


def test_mild_recovery_compromise_returns_maintenance():
    s = RegulationSignals(
        hrv_z_3d=-0.6, rhr_z_3d=0.4, sleep_3d_min=380,
        sleep_debt_min=120, strain_7d_total=70,
        subjective_3d_energy=6, days_with_complete_data=3,
    )
    rec, _, _ = regulate(s)
    assert rec == "maintenance"


def test_high_7d_strain_triggers_deficit_conservative():
    s = RegulationSignals(
        hrv_z_3d=0.0, rhr_z_3d=0.0, sleep_3d_min=420,
        sleep_debt_min=60, strain_7d_total=110,  # 110/7 = 15.7/day, > 15 threshold
        subjective_3d_energy=7, days_with_complete_data=3,
    )
    rec, _, payload = regulate(s)
    assert rec == "deficit_conservative"
    assert payload["kcal"] == 2500


def test_all_green_returns_deficit():
    s = RegulationSignals(
        hrv_z_3d=0.4, rhr_z_3d=-0.2, sleep_3d_min=450,
        sleep_debt_min=0, strain_7d_total=70,  # 70/7 = 10/day, < 13
        subjective_3d_energy=8, days_with_complete_data=3,
    )
    rec, _, payload = regulate(s)
    assert rec == "deficit"
    assert payload["kcal"] == 2300


def test_subjective_none_does_not_trigger_collapse():
    s = RegulationSignals(
        hrv_z_3d=0.0, rhr_z_3d=0.0, sleep_3d_min=420,
        sleep_debt_min=60, strain_7d_total=80,
        subjective_3d_energy=None, days_with_complete_data=2,
    )
    rec, _, _ = regulate(s)
    # No subjective → should not return deload from energy-collapse branch
    assert rec != "deload" or "Subjective" not in (rec or "")


@pytest.mark.asyncio
async def test_compute_signals_with_three_days_data(db_session, test_user_id):
    # Seed 3 days of daily_metrics with z-scores and one manual_log entry.
    await db_session.execute(text("""
        INSERT INTO daily_metrics (user_id, metric_date,
            oura_hrv_avg, oura_rhr, oura_sleep_duration_min,
            unified_hrv_z, unified_rhr_z, whoop_sleep_debt_min,
            whoop_day_strain, oura_status, whoop_status, ingestion_complete)
        VALUES
            (:u, :d1, 45, 60, 400, -1.0, 0.5, 200, 12.0, 'ok', 'ok', TRUE),
            (:u, :d2, 47, 58, 410, -0.8, 0.3, 180, 11.0, 'ok', 'ok', TRUE),
            (:u, :d3, 46, 59, 380, -1.2, 0.7, 220, 13.0, 'ok', 'ok', TRUE)
    """), {"u": test_user_id, "d1": date(2026, 5, 11), "d2": date(2026, 5, 12), "d3": date(2026, 5, 13)})
    await db_session.execute(text("""
        INSERT INTO manual_log (user_id, log_date,
            subjective_energy, subjective_mood, subjective_hunger)
        VALUES
            (:u, :d1, 6, 7, 5),
            (:u, :d2, 7, 7, 6),
            (:u, :d3, 6, 6, 5)
    """), {"u": test_user_id, "d1": date(2026, 5, 11), "d2": date(2026, 5, 12), "d3": date(2026, 5, 13)})

    signals = await compute_regulation_signals(
        db_session, user_id=test_user_id, anchor=date(2026, 5, 13)
    )
    # HRV z avg of -1.0, -0.8, -1.2 = -1.0
    assert signals.hrv_z_3d == pytest.approx(-1.0, abs=0.01)
    # Sleep avg of 400, 410, 380 = 396.67
    assert signals.sleep_3d_min == pytest.approx(396.67, abs=0.5)
    # Subjective energy avg 6, 7, 6 = 6.33
    assert signals.subjective_3d_energy == pytest.approx(6.33, abs=0.01)
    assert signals.days_with_complete_data == 3


@pytest.mark.asyncio
async def test_compute_signals_with_no_data_returns_none_sleep(db_session, test_user_id):
    """Missing-data is signaled with sleep_3d_min=None, NOT 0.0 — preventing the
    false-positive 'severe sleep deprivation' DELOAD that the < 300 floor would
    otherwise trigger. See regulate()'s None short-circuit."""
    signals = await compute_regulation_signals(
        db_session, user_id=test_user_id, anchor=date(2026, 5, 13)
    )
    assert signals.days_with_complete_data == 0
    assert signals.subjective_3d_energy is None
    assert signals.sleep_3d_min is None


def test_missing_sleep_short_circuits_to_maintenance_not_deload():
    """When sleep_3d_min is None (no Oura sleep data), regulate() must NOT
    silently treat it as 0 and trigger the < 300 'severe sleep deprivation'
    DELOAD. Should default to maintenance with a clear no-data rationale."""
    s = RegulationSignals(
        hrv_z_3d=0.0, rhr_z_3d=0.0, sleep_3d_min=None,
        sleep_debt_min=0.0, strain_7d_total=70,
        subjective_3d_energy=None, days_with_complete_data=0,
    )
    rec, rationale, payload = regulate(s)
    assert rec == "maintenance"
    assert any("sleep data unavailable" in r.lower() for r in rationale)


def test_missing_sleep_with_real_whoop_strain_still_maintenance():
    """Even with elevated Whoop strain data, missing sleep must not flip to
    deload — the conservative default is maintenance until sleep is known."""
    s = RegulationSignals(
        hrv_z_3d=-0.5, rhr_z_3d=0.5, sleep_3d_min=None,
        sleep_debt_min=0.0, strain_7d_total=110,  # would be deficit_conservative with data
        subjective_3d_energy=None, days_with_complete_data=0,
    )
    rec, _, _ = regulate(s)
    assert rec == "maintenance"
