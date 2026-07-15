from health_metrics.regulation.energy_config import (
    NORMALIZE_TYPE,
    PER_TYPE_KCAL_PER_MIN,
    get_energy_params,
)


def test_default_params_present():
    p = get_energy_params("hugo")
    assert 1.2 <= p.baseline_activity_factor <= 1.5
    assert p.neat_coef > 0
    assert p.fallback_rmr_kcal > 0
    assert 0 < p.divergence_pct < 1


def test_unknown_user_falls_back():
    assert get_energy_params("nobody") == get_energy_params("hugo")


def test_type_normalization_covers_whoop_types():
    assert NORMALIZE_TYPE["walking"] == "walk"
    assert NORMALIZE_TYPE["cycling"] == "ride"
    assert NORMALIZE_TYPE["functional-fitness"] == "strength"


def test_per_type_kcal_has_default_key():
    assert "other" in PER_TYPE_KCAL_PER_MIN
