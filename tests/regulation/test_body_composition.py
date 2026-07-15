from health_metrics.regulation.body_composition import katch_mcardle_rmr


def test_katch_mcardle_known_value():
    # 170 lb lean mass = 77.11 kg → 370 + 21.6*77.11 = 2035.7 → 2036
    assert katch_mcardle_rmr(170.0) == 2036


def test_katch_mcardle_monotonic():
    assert katch_mcardle_rmr(180.0) > katch_mcardle_rmr(150.0)
