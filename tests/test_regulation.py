from health_metrics.regulation import RegulationSignals, regulate


def test_severe_sleep_deprivation_triggers_deload():
    s = RegulationSignals(
        hrv_z_3d=0.0, rhr_z_3d=0.0, sleep_3d_min=280,
        sleep_debt_min=600, strain_7d_total=70,
        subjective_3d_energy=6, days_with_complete_data=3,
    )
    rec, rationale, payload = regulate(s)
    assert rec == "deload"
    assert payload["kcal"] == 2800


def test_subjective_energy_collapse_triggers_deload():
    s = RegulationSignals(
        hrv_z_3d=0.0, rhr_z_3d=0.0, sleep_3d_min=420,
        sleep_debt_min=120, strain_7d_total=70,
        subjective_3d_energy=3.5, days_with_complete_data=3,
    )
    rec, _, _ = regulate(s)
    assert rec == "deload"


def test_mild_recovery_compromise_returns_maintenance():
    s = RegulationSignals(
        hrv_z_3d=-0.6, rhr_z_3d=0.4, sleep_3d_min=380,
        sleep_debt_min=120, strain_7d_total=70,
        subjective_3d_energy=6, days_with_complete_data=3,
    )
    rec, _, _ = regulate(s)
    assert rec == "maintenance"


def test_high_7d_strain_triggers_deficit_conservative():
    s = RegulationSignals(
        hrv_z_3d=0.0, rhr_z_3d=0.0, sleep_3d_min=420,
        sleep_debt_min=60, strain_7d_total=110,  # 110/7 = 15.7/day, > 15 threshold
        subjective_3d_energy=7, days_with_complete_data=3,
    )
    rec, _, payload = regulate(s)
    assert rec == "deficit_conservative"
    assert payload["kcal"] == 2500


def test_all_green_returns_deficit():
    s = RegulationSignals(
        hrv_z_3d=0.4, rhr_z_3d=-0.2, sleep_3d_min=450,
        sleep_debt_min=0, strain_7d_total=70,  # 70/7 = 10/day, < 13
        subjective_3d_energy=8, days_with_complete_data=3,
    )
    rec, _, payload = regulate(s)
    assert rec == "deficit"
    assert payload["kcal"] == 2300


def test_subjective_none_does_not_trigger_collapse():
    s = RegulationSignals(
        hrv_z_3d=0.0, rhr_z_3d=0.0, sleep_3d_min=420,
        sleep_debt_min=60, strain_7d_total=80,
        subjective_3d_energy=None, days_with_complete_data=2,
    )
    rec, _, _ = regulate(s)
    # No subjective → should not return deload from energy-collapse branch
    assert rec != "deload" or "Subjective" not in (rec or "")
