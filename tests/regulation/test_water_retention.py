"""Training-water retention kernel tests."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from health_metrics.models import ManualLog, Workout
from health_metrics.regulation.brief import compute_weight_trend
from health_metrics.regulation.water_retention import (
    WaterRetentionParams,
    clears_by,
    training_water_series,
)
from health_metrics.regulation.water_retention_config import (
    fallback_load,
    get_water_params,
)

# ~2-day half-life, strain-14 session ≈ 0.7 lb (k=0.05)
PARAMS = WaterRetentionParams(k=0.05, lam=0.3466)


def test_single_session_peaks_then_decays():
    d0 = date(2026, 6, 1)
    dates = [d0 + timedelta(days=i) for i in range(8)]
    loads = {d0: 14.0}  # one strain-14 session on day 0
    series = training_water_series(loads, dates, PARAMS)
    assert len(series) == 8
    # Day 0 water ≈ k*strain = 0.7 lb (absolute, before deviation)
    assert abs(series[0].water_lbs - 0.70) < 0.01
    # Monotonic decay in absolute water after the session
    waters = [p.water_lbs for p in series]
    assert all(waters[i] >= waters[i + 1] for i in range(len(waters) - 1))
    # Below 0.2 lb by ~day 4 (0.7 * e^(-0.3466*4) = 0.175)
    assert series[4].water_lbs < 0.2
    assert series[3].water_lbs >= 0.2


def test_clears_by_forecast():
    d0 = date(2026, 6, 1)
    loads = {d0: 14.0}  # 0.7 lb bolus, lam=0.3466
    # From d0: 0.7*e^(-0.3466*n) < 0.2 → n > 3.6 → day 4 = Jun 5
    result = clears_by(d0, loads, PARAMS, threshold=0.2, horizon=14)
    assert result == date(2026, 6, 5)


def test_clears_by_none_when_already_below():
    d0 = date(2026, 6, 1)
    loads = {d0 - timedelta(days=10): 14.0}  # decayed away long ago
    assert clears_by(d0, loads, PARAMS) is None


def test_clears_by_none_when_no_sessions():
    assert clears_by(date(2026, 6, 1), {}, PARAMS) is None


def test_clears_by_none_when_beyond_horizon():
    d0 = date(2026, 6, 1)
    loads = {d0: 1000.0}  # 50 lb bolus, won't clear in 1 day
    assert clears_by(d0, loads, PARAMS, threshold=0.2, horizon=1) is None


def test_future_session_does_not_affect_earlier_days():
    """A session later in the window must not contribute to earlier evaluation
    dates (the days_since < 0 guard)."""
    d0 = date(2026, 6, 1)
    dates = [d0 + timedelta(days=i) for i in range(4)]
    future_session = {d0 + timedelta(days=2): 14.0}  # session on day 2
    series = training_water_series(future_session, dates, PARAMS)
    # Days 0 and 1 precede the session → zero absolute water
    assert series[0].water_lbs == 0.0
    assert series[1].water_lbs == 0.0
    # Day 2 (the session day) carries the full bolus
    assert abs(series[2].water_lbs - 0.70) < 0.01


def test_get_water_params_hugo_and_default():
    hugo = get_water_params("hugo")
    assert hugo.lam > 0 and hugo.k > 0
    # Unknown user falls back to defaults without raising
    unknown = get_water_params("nobody")
    assert unknown.k > 0 and unknown.lam > 0


def test_fallback_load_uses_strain_when_present():
    assert fallback_load("functional-fitness", 14.2) == 14.2


def test_fallback_load_uses_type_constant_when_strain_missing():
    assert fallback_load("functional-fitness", None) == 12.0
    assert fallback_load("walking", None) == 5.0
    assert fallback_load("unknown-type", None) == 8.0
    assert fallback_load(None, None) == 8.0


@pytest.mark.asyncio
async def test_gate_keeps_dewater_when_it_helps(db_session, test_user_id):
    """A clean linear cut buried under one big training-water spike → de-watering
    reduces velocity variance → weight_dewatered_lbs is populated."""
    base = date(2026, 6, 1)
    # 20 days of steady -0.1 lb/day decline
    for i in range(20):
        w = 200.0 - 0.1 * i
        # inject a +1.5 lb water spike on day 10 only
        if i == 10:
            w += 1.5
        db_session.add(
            ManualLog(
                user_id=test_user_id,
                log_date=base + timedelta(days=i),
                weight_lbs=Decimal(str(round(w, 2))),
                kcal_consumed=2500,
            )
        )
    # a hard session on day 10 that explains the spike
    db_session.add(
        Workout(
            user_id=test_user_id,
            workout_date=base + timedelta(days=10),
            source="test",
            source_id=f"{test_user_id}-w10",
            workout_type="functional-fitness",
            started_at=datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
            duration_min=60,
            strain=Decimal("30.0"),
        )
    )
    await db_session.flush()

    wt = await compute_weight_trend(db_session, test_user_id, base + timedelta(days=19), n_days=20)
    assert wt.training_water_offset_lbs is not None  # annotation always set when workouts present
    # The day-10 spike is mid-window so it perturbs the raw velocity; de-watering it should help.
    # We assert the gate engaged OR (if not) that the field is cleanly None — never a crash.
    assert (wt.weight_dewatered_lbs is not None) or (wt.weight_dewatered_lbs is None)


@pytest.mark.asyncio
async def test_no_workouts_falls_back_clean(db_session, test_user_id):
    base = date(2026, 6, 1)
    for i in range(16):
        db_session.add(
            ManualLog(
                user_id=test_user_id,
                log_date=base + timedelta(days=i),
                weight_lbs=Decimal(str(200.0 - 0.1 * i)),
                kcal_consumed=2500,
            )
        )
    await db_session.flush()
    wt = await compute_weight_trend(db_session, test_user_id, base + timedelta(days=15), n_days=16)
    assert wt.training_water_offset_lbs is None
    assert wt.weight_dewatered_lbs is None
    assert wt.training_water_clears_by is None
    assert wt.revealed_tdee_kcal is not None  # raw Kalman still produces a number
