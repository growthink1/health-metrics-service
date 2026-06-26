"""Integration / characterization test: filtered revealed_tdee on a realistic
69-day series has <40% the std-dev of the raw-endpoint method (>60% reduction).
Reproduces the prod whipsaw and proves the fix kills it."""

import json
import statistics
from datetime import date
from pathlib import Path

from health_metrics.regulation.kalman import kalman_weight

FIXTURE = Path(__file__).parent.parent / "fixtures" / "days" / "kalman-real-shape-69d.json"


def _load_observations() -> tuple[list[tuple[date, float | None]], float]:
    data = json.loads(FIXTURE.read_text())
    out: list[tuple[date, float | None]] = []
    for row in data["observations"]:
        d = date.fromisoformat(row["date"])
        w = row["weight_lbs"]
        out.append((d, w if w is not None else None))
    return out, data["true_slope_lbs_per_day"]


def test_filtered_revealed_tdee_variance_is_at_least_60pct_lower_than_raw() -> None:
    """For each rolling 14-day window, compute revealed_tdee both ways. The std-dev
    of the filtered series must be at least 60% smaller than the std-dev of the
    raw-endpoint series."""
    obs, _true_slope = _load_observations()
    # Compute Kalman over the full series (the filter integrates information
    # across the whole window, so we just take the final velocity at each day's
    # backward 14-day cutoff)
    avg_intake_kcal = 2900  # held fixed; the variance comes from the velocity term

    filtered_tdee_series: list[float] = []
    raw_tdee_series: list[float] = []
    for window_end in range(14, len(obs)):
        window = obs[window_end - 14 : window_end + 1]
        # Filtered: run kalman, take final velocity
        pts = kalman_weight(window)
        if not pts:
            continue
        filtered_velocity = pts[-1].velocity
        filtered_tdee_series.append(avg_intake_kcal - filtered_velocity * 3500.0)

        # Raw: endpoint delta
        observed = [(d, w) for d, w in window if w is not None]
        if len(observed) < 2:
            continue
        endpoint_delta = observed[-1][1] - observed[0][1]
        endpoint_days = (observed[-1][0] - observed[0][0]).days
        if endpoint_days == 0:
            continue
        raw_velocity = endpoint_delta / endpoint_days
        raw_tdee_series.append(avg_intake_kcal - raw_velocity * 3500.0)

    filtered_sd = statistics.stdev(filtered_tdee_series)
    raw_sd = statistics.stdev(raw_tdee_series)
    reduction = 1.0 - (filtered_sd / raw_sd)
    print(f"\nfiltered_sd={filtered_sd:.1f}  raw_sd={raw_sd:.1f}  reduction={reduction:.2%}")
    assert reduction > 0.6, (
        f"expected >60% std-dev reduction, got {reduction:.2%} (filtered_sd={filtered_sd:.1f}, raw_sd={raw_sd:.1f})"
    )
