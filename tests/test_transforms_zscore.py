from health_metrics.transforms.zscore import compute_zscore


def test_zscore_returns_none_for_short_baseline():
    assert compute_zscore(50.0, [48.0, 49.0]) is None


def test_zscore_returns_zero_for_zero_variance():
    assert compute_zscore(50.0, [50.0] * 7) == 0.0


def test_zscore_computes_above_baseline():
    baseline = [48.0, 49.0, 50.0, 51.0, 52.0, 50.0, 49.0]
    z = compute_zscore(55.0, baseline)
    assert z is not None
    assert z > 1.0


def test_zscore_computes_below_baseline():
    baseline = [48.0, 49.0, 50.0, 51.0, 52.0, 50.0, 49.0]
    z = compute_zscore(45.0, baseline)
    assert z is not None
    assert z < -1.0
