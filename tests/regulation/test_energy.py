from health_metrics.regulation.energy import (
    Activity,
    compute_energy,
    dedup_activities,
    neat_kcal,
)
from health_metrics.regulation.energy_config import get_energy_params

P = get_energy_params("hugo")


def test_dedup_manual_wins_over_auto_same_type():
    acts = [
        Activity("walk", "auto", None, 60, 90.0),
        Activity("walk", "manual", 2.7, 55, None),
    ]
    out = dedup_activities(acts)
    assert len(out) == 1
    assert out[0].source_layer == "manual"


def test_dedup_keeps_distinct_types():
    acts = [
        Activity("walk", "auto", None, 60, 90.0),
        Activity("ride", "auto", None, 41, 442.0),
    ]
    assert len(dedup_activities(acts)) == 2


def test_neat_distance_formula():
    # 2.7 mi * 220 lb * neat_coef; distance path is already net-of-resting (rmr not used here).
    a = Activity("walk", "manual", 2.7, 55, None)
    val = neat_kcal([a], 220.0, P, rmr_kcal=1812)
    assert abs(val - (2.7 * 220.0 * P.neat_coef)) < 0.01


def test_measured_kcal_is_net_of_resting():
    # Whoop workouts.kcal is GROSS; subtract in-workout resting (rmr/1440 * duration).
    a = Activity("ride", "auto", None, 60, 500.0)
    rmr = 1800
    expected = 500.0 - rmr / 1440.0 * 60  # 500 - 75 = 425
    assert abs(neat_kcal([a], 220.0, P, rmr_kcal=rmr) - expected) < 0.01


def test_measured_kcal_net_floored_at_zero():
    # A tiny/low workout whose gross < in-workout resting must floor at 0, not go negative.
    a = Activity("walk", "auto", None, 60, 40.0)
    rmr = 1800  # resting for 60 min = 75 > 40
    assert neat_kcal([a], 220.0, P, rmr_kcal=rmr) == 0.0


def test_measured_kcal_without_duration_uses_gross():
    # Can't net-correct without a duration -> fall back to the gross value.
    a = Activity("ride", "auto", None, None, 300.0)
    assert neat_kcal([a], 220.0, P, rmr_kcal=1800) == 300.0


def test_neat_duration_fallback_when_no_distance_or_kcal():
    a = Activity("strength", "manual", None, 45, None)
    from health_metrics.regulation.energy_config import PER_TYPE_KCAL_PER_MIN

    assert neat_kcal([a], 220.0, P, rmr_kcal=1812) == 45 * PER_TYPE_KCAL_PER_MIN["strength"]


def test_compute_energy_headline_is_modeled():
    acts = [Activity("walk", "manual", 2.7, 55, None)]
    e = compute_energy(2000, "dexa", 220.0, acts, whoop_kcal_burned=2650, whoop_complete=True, params=P)
    assert e.baseline_kcal == round(2000 * P.baseline_activity_factor)
    assert e.tdee_modeled_kcal == e.baseline_kcal + round(e.neat_kcal)
    assert e.tdee_estimate_kcal == e.tdee_modeled_kcal  # headline = modeled
    assert e.tdee_measured_kcal == 2650


def test_compute_energy_net_corrects_measured_activity():
    # compute_energy threads its rmr_kcal into the NEAT net correction.
    acts = [Activity("ride", "auto", None, 60, 500.0)]
    e = compute_energy(1800, "dexa", 220.0, acts, whoop_kcal_burned=None, whoop_complete=False, params=P)
    assert abs(e.neat_kcal - (500.0 - 1800 / 1440.0 * 60)) < 0.1  # 425


def test_compute_energy_partial_day_excludes_measured():
    e = compute_energy(2000, "dexa", 220.0, [], whoop_kcal_burned=807, whoop_complete=False, params=P)
    assert e.tdee_measured_kcal is None
    assert e.divergence_flag is False


def test_compute_energy_divergence_flag():
    # modeled ~2600, measured 2000 → >10% divergence
    e = compute_energy(2000, "dexa", 220.0, [], whoop_kcal_burned=2000, whoop_complete=True, params=P)
    assert e.tdee_modeled_kcal == round(2000 * P.baseline_activity_factor)
    assert e.divergence_flag is True


def test_active_day_beats_sedentary_day():
    sed = compute_energy(2000, "dexa", 220.0, [], None, False, P)
    act = compute_energy(2000, "dexa", 220.0, [Activity("walk", "manual", 2.7, 55, None)], None, False, P)
    assert act.tdee_estimate_kcal > sed.tdee_estimate_kcal
