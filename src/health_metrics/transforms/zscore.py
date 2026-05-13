"""14-day rolling z-score computation."""

import statistics

MIN_BASELINE = 7  # need at least 7 values to compute meaningful z


def compute_zscore(value: float, baseline_values: list[float]) -> float | None:
    """
    Return the z-score of `value` against `baseline_values`.

    The caller is responsible for excluding `value` itself from the baseline
    list. Returns None if baseline is too small to be meaningful. Returns
    0.0 if baseline has zero variance (don't treat 0.0 as missing data).
    """
    if len(baseline_values) < MIN_BASELINE:
        return None
    mean = statistics.mean(baseline_values)
    std = statistics.stdev(baseline_values)
    if std == 0:
        return 0.0
    return (value - mean) / std
