from datetime import date, timedelta

from health_metrics.jobs.projection import project_hrv


def test_hrv_rising_trend_positive_slope():
    obs = [(date(2026, 3, 1) + timedelta(days=i), 40 + 0.05 * i) for i in range(60)]
    result = project_hrv(
        daily_hrv=obs,
        current_date=date(2026, 4, 30),
        target_value=50.0,
        target_date=date(2026, 7, 1),
    )
    assert result["method"] == "bayesian_normal_normal"
    assert result["posterior_params"]["weekly_slope_mean"] > 0


def test_hrv_insufficient_data():
    obs = [(date(2026, 4, 1) + timedelta(days=i), 45.0) for i in range(15)]
    result = project_hrv(
        daily_hrv=obs,
        current_date=date(2026, 4, 16),
        target_value=50.0,
        target_date=date(2026, 7, 1),
    )
    assert result["method"] == "insufficient_data"
