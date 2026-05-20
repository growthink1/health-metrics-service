from datetime import date, timedelta

from health_metrics.jobs.projection import project_habit


def test_habit_consistent_4_per_week_p_on_pace_high():
    obs = [(date(2026, 4, 1) + timedelta(days=i), (i % 7) < 4) for i in range(28)]
    result = project_habit(day_workouts=obs, current_date=date(2026, 4, 29), target_value=4.0)
    assert result["method"] == "beta_binomial"
    assert result["p_on_pace"] > 0.4
    assert 3.0 < result["projected_value_mean"] < 5.0


def test_habit_zero_workouts_p_on_pace_low():
    obs = [(date(2026, 4, 1) + timedelta(days=i), False) for i in range(28)]
    result = project_habit(day_workouts=obs, current_date=date(2026, 4, 29), target_value=4.0)
    assert result["p_on_pace"] < 0.1


def test_habit_insufficient_data():
    obs = [(date(2026, 4, 27) + timedelta(days=i), True) for i in range(3)]
    result = project_habit(day_workouts=obs, current_date=date(2026, 4, 30), target_value=4.0)
    assert result["method"] == "insufficient_data"
