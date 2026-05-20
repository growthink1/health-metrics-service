"""Bayesian Normal-Normal weight model."""

from datetime import date, timedelta

from health_metrics.jobs.projection import project_weight


def _series(start_date, days, slope_per_day, base):
    """Build a synthetic [(date, value), ...] with linear trend."""
    return [(start_date + timedelta(days=i), base + slope_per_day * i) for i in range(days)]


def test_weight_loss_trend_posterior_mean_near_truth():
    data = _series(date(2026, 4, 1), 30, slope_per_day=-0.10, base=200.0)
    target_date = date(2026, 7, 1)
    result = project_weight(
        observations=data,
        current_date=date(2026, 5, 1),
        target_value=185.0,
        target_date=target_date,
        goal_direction="down",
    )
    assert result["method"] == "bayesian_normal_normal"
    # Slope is -0.10 lb/day = -0.7 lb/wk. Prior -0.5 sigma=0.5. Posterior should fall between.
    assert -0.95 < result["posterior_params"]["slope_mean"] < -0.55
    # 95% CI must contain the true projection
    truth = 200.0 + (-0.10) * (date(2026, 7, 1) - date(2026, 4, 1)).days
    assert result["projected_value_ci_low"] <= truth <= result["projected_value_ci_high"]


def test_weight_insufficient_data_returns_fallback():
    data = _series(date(2026, 4, 28), 5, slope_per_day=-0.10, base=200.0)
    result = project_weight(
        observations=data,
        current_date=date(2026, 5, 1),
        target_value=185.0,
        target_date=date(2026, 7, 1),
        goal_direction="down",
    )
    assert result["method"] == "insufficient_data"
    assert result["data_points_used"] == 5
    assert result["min_required"] == 7


def test_weight_zero_variance_falls_back_gracefully():
    data = [(date(2026, 4, 1) + timedelta(days=i), 200.0) for i in range(30)]
    result = project_weight(
        observations=data,
        current_date=date(2026, 5, 1),
        target_value=185.0,
        target_date=date(2026, 7, 1),
        goal_direction="down",
    )
    # Zero variance is fine - posterior slope should be very close to prior (-0.5 lb/wk)
    assert result["method"] == "bayesian_normal_normal"
    assert abs(result["posterior_params"]["slope_mean"] - (-0.5)) < 0.05


def test_weight_p_on_pace_low_when_off_track():
    data = _series(date(2026, 4, 1), 30, slope_per_day=-0.05, base=200.0)
    result = project_weight(
        observations=data,
        current_date=date(2026, 5, 1),
        target_value=185.0,
        target_date=date(2026, 7, 1),
        goal_direction="down",
    )
    # 5 lb/100d trajectory won't hit 15 lb deficit in 2 months
    assert result["p_on_pace"] < 0.35
