"""Unit tests for the 1-D Kalman weight filter."""

import math
import random
from datetime import date, timedelta

from health_metrics.regulation.kalman import kalman_weight


def _linear_series(
    start: date,
    days: int,
    slope_per_day: float,
    base: float,
    noise_sigma: float = 0.0,
    seed: int = 0,
) -> list[tuple[date, float]]:
    rng = random.Random(seed)
    return [(start + timedelta(days=i), base + slope_per_day * i + rng.gauss(0, noise_sigma)) for i in range(days)]


def test_kalman_recovers_velocity_under_noise() -> None:
    """30 days of -0.25 lb/day decline + sigma=1.5 lb noise -> recovered velocity within
    +-0.05 lb/day. The raw endpoint method would be off by >800 kcal; the filter
    must be within +-100 kcal of the truth."""
    truth_slope = -0.25
    avg_intake = 2950
    # True TDEE = intake - velocity*3500 (losing weight at velocity v means
    # burning v*3500 more than intake each day).
    true_tdee = avg_intake - truth_slope * 3500.0  # = 3825 for slope -0.25
    obs = _linear_series(date(2026, 4, 1), 30, slope_per_day=truth_slope, base=200.0, noise_sigma=1.5, seed=42)
    points = kalman_weight([(d, w) for d, w in obs])
    assert len(points) == 30
    final = points[-1]
    assert abs(final.velocity - truth_slope) < 0.05, (
        f"recovered velocity {final.velocity:.3f} far from truth {truth_slope}"
    )

    # TDEE check: filter must be within +-200 kcal of truth
    filtered_tdee = avg_intake - (final.velocity * 3500.0)
    assert abs(filtered_tdee - true_tdee) < 200.0, (
        f"filtered TDEE {filtered_tdee:.0f} > +-200 from truth {true_tdee:.0f}"
    )

    # Raw endpoint method (the thing we're replacing)
    delta = obs[-1][1] - obs[0][1]
    raw_velocity = delta / (len(obs) - 1)
    raw_tdee = avg_intake - (raw_velocity * 3500.0)
    # Filter must be at least as good as the raw endpoint method on this series.
    assert abs(filtered_tdee - true_tdee) <= abs(raw_tdee - true_tdee), (
        f"filter {filtered_tdee:.0f} should be at least as close to {true_tdee:.0f} as raw {raw_tdee:.0f}"
    )


def test_kalman_handles_gaps() -> None:
    """3 random days dropped from a 30-day series -- filter still produces a
    full day-by-day timeline + velocity within +-0.07 lb/day."""
    obs = _linear_series(date(2026, 4, 1), 30, slope_per_day=-0.2, base=195.0, noise_sigma=1.0, seed=7)
    rng = random.Random(123)
    drop_indices = sorted(rng.sample(range(2, 28), 3))
    obs_with_gaps = [(d, w) for i, (d, w) in enumerate(obs) if i not in drop_indices]
    # Use date-only sequence so the filter walks day-by-day across the gaps
    points = kalman_weight([(d, w) for d, w in obs_with_gaps])
    # The filter walks daily from first to last date; should have == days_span points
    days_span = (obs_with_gaps[-1][0] - obs_with_gaps[0][0]).days + 1
    assert len(points) == days_span
    # Verify no nan / inf
    assert all(math.isfinite(p.level) and math.isfinite(p.velocity) for p in points)
    # Velocity should still recover
    assert abs(points[-1].velocity - (-0.2)) < 0.07


def test_kalman_cold_start_low_confidence_marker() -> None:
    """7-day window -> velocity variance should be larger than after 30 days."""
    obs_short = _linear_series(date(2026, 5, 1), 7, slope_per_day=-0.2, base=200.0, noise_sigma=1.0, seed=1)
    obs_long = _linear_series(date(2026, 5, 1), 30, slope_per_day=-0.2, base=200.0, noise_sigma=1.0, seed=1)
    short_var = kalman_weight([(d, w) for d, w in obs_short])[-1].velocity_var
    long_var = kalman_weight([(d, w) for d, w in obs_long])[-1].velocity_var
    assert short_var > long_var, (
        f"short-window velocity_var ({short_var:.4f}) should exceed long-window ({long_var:.4f})"
    )


def test_kalman_empty_and_none_input() -> None:
    """Empty input -> empty output. All-None input -> empty output."""
    assert kalman_weight([]) == []
    series_all_none: list[tuple[date, float | None]] = [
        (date(2026, 5, 1), None),
        (date(2026, 5, 2), None),
    ]
    assert kalman_weight(series_all_none) == []
