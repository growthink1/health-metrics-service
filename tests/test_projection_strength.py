from datetime import date, timedelta

from health_metrics.jobs.projection import project_strength


def test_strength_growing_trend_posterior_positive():
    obs = [(date(2026, 3, 1) + timedelta(days=14 * i), 300 + 5 * i) for i in range(5)]
    result = project_strength(
        pr_observations=obs,
        current_date=date(2026, 5, 1),
        target_value=350.0,
        target_date=date(2026, 9, 1),
    )
    assert result["method"] == "bayesian_normal_normal"
    assert result["posterior_params"]["weekly_pct_gain_mean"] > 0


def test_strength_insufficient_data():
    obs = [(date(2026, 4, 1) + timedelta(days=7 * i), 300 + i) for i in range(3)]
    result = project_strength(
        pr_observations=obs,
        current_date=date(2026, 5, 1),
        target_value=350.0,
        target_date=date(2026, 9, 1),
    )
    assert result["method"] == "insufficient_data"
