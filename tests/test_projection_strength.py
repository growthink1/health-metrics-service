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


def test_strength_ci_contains_truth():
    # 5 PRs over 8 weeks at +1% per week from 300 lbs base
    obs = [(date(2026, 3, 1) + timedelta(days=14 * i), 300.0 * (1.01 ** (2 * i))) for i in range(5)]
    result = project_strength(
        pr_observations=obs,
        current_date=date(2026, 4, 26),
        target_value=350.0,
        target_date=date(2026, 7, 1),
    )
    # 9 weeks after current_date with ~1%/wk gain compounding from current value
    weeks_remaining = (date(2026, 7, 1) - date(2026, 4, 26)).days / 7.0
    current = result["current_value"]
    truth = current * (1.01**weeks_remaining)
    assert result["projected_value_ci_low"] <= truth <= result["projected_value_ci_high"]
