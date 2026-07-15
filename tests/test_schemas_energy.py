from health_metrics.regulation.schemas import EnergyToday, SessionBrief


def test_energy_today_shape():
    e = EnergyToday(
        neat_kcal=120.0,
        baseline_kcal=2700,
        rmr_kcal=2000,
        tdee_measured_kcal=2650,
        tdee_modeled_kcal=2820,
        tdee_estimate_kcal=2820,
        divergence_flag=False,
        activities_counted=["walk 2.7mi (activity_log)"],
        rmr_source="dexa",
    )
    assert e.tdee_estimate_kcal == 2820


def test_session_brief_energy_today_optional():
    assert "energy_today" in SessionBrief.model_fields
