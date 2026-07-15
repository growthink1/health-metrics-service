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
    # 2.7 mi * 220 lb * 0.53 = 314.8 ... seed coef; assert the arithmetic path
    a = Activity("walk", "manual", 2.7, 55, None)
    val = neat_kcal([a], 220.0, P)
    assert abs(val - (2.7 * 220.0 * P.neat_coef)) < 0.01


def test_neat_prefers_measured_kcal():
    a = Activity("ride", "auto", None, 41, 442.0)
    assert neat_kcal([a], 220.0, P) == 442.0


def test_neat_duration_fallback_when_no_distance_or_kcal():
    a = Activity("strength", "manual", None, 45, None)
    from health_metrics.regulation.energy_config import PER_TYPE_KCAL_PER_MIN
    assert neat_kcal([a], 220.0, P) == 45 * PER_TYPE_KCAL_PER_MIN["strength"]


def test_compute_energy_headline_is_modeled():
    acts = [Activity("walk", "manual", 2.7, 55, None)]
    e = compute_energy(2000, "dexa", 220.0, acts, whoop_kcal_burned=2650, whoop_complete=True, params=P)
    assert e.baseline_kcal == round(2000 * P.baseline_activity_factor)
    assert e.tdee_modeled_kcal == e.baseline_kcal + round(e.neat_kcal)
    assert e.tdee_estimate_kcal == e.tdee_modeled_kcal  # headline = modeled
    assert e.tdee_measured_kcal == 2650


def test_compute_energy_partial_day_excludes_measured():
    e = compute_energy(2000, "dexa", 220.0, [], whoop_kcal_burned=807, whoop_complete=False, params=P)
    assert e.tdee_measured_kcal is None
    assert e.divergence_flag is False


def test_compute_energy_divergence_flag():
    # modeled ~2700, measured 2000 → >10% divergence
    e = compute_energy(2000, "dexa", 220.0, [], whoop_kcal_burned=2000, whoop_complete=True, params=P)
    assert e.tdee_modeled_kcal == round(2000 * P.baseline_activity_factor)
    assert e.divergence_flag is True


def test_active_day_beats_sedentary_day():
    sed = compute_energy(2000, "dexa", 220.0, [], None, False, P)
    act = compute_energy(2000, "dexa", 220.0, [Activity("walk", "manual", 2.7, 55, None)], None, False, P)
    assert act.tdee_estimate_kcal > sed.tdee_estimate_kcal
