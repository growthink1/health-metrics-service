"""Tests for compute_legacy_recommendation + map_to_legacy."""

from health_metrics.regulation.legacy_adapter import map_to_legacy
from health_metrics.regulation.schemas import (
    RegulationCall,
    RegulationState,
    TrainingModifier,
)


def _call(state: RegulationState, kcal: int = 2300) -> RegulationCall:
    return RegulationCall(
        state=state,
        training_modifier=TrainingModifier.FULL_PROGRESSION,
        kcal_target=kcal,
        rationale=["test"],
        confidence="high",
    )


def test_deficit_maps_to_legacy_deficit():
    rec, rationale, action = map_to_legacy(_call(RegulationState.DEFICIT, 2300))
    assert rec == "deficit"
    assert rationale == ["test"]
    assert action == {"kcal": 2300, "training": "Full program, progression OK"}


def test_deficit_conservative_maps_to_legacy_bucket():
    rec, _, action = map_to_legacy(_call(RegulationState.DEFICIT_CONSERVATIVE, 2500))
    assert rec == "deficit_conservative"
    assert action["kcal"] == 2500


def test_sleep_deficit_maps_to_deload():
    rec, _, action = map_to_legacy(_call(RegulationState.MAINTENANCE_SLEEP_DEFICIT, 2800))
    assert rec == "deload"
    assert "Z2 only" in action["training"]


def test_illness_maps_to_deload_rest():
    rec, _, action = map_to_legacy(_call(RegulationState.MAINTENANCE_ILLNESS, 2800))
    assert rec == "deload"
    assert "REST" in action["training"]


def test_pre_procedure_maps_to_maintenance():
    rec, _, action = map_to_legacy(_call(RegulationState.MAINTENANCE_PRE_PROCEDURE, 2800))
    assert rec == "maintenance"
    assert "Pre-procedure" in action["training"]


def test_hrv_depression_maps_to_maintenance():
    rec, _, action = map_to_legacy(_call(RegulationState.MAINTENANCE_HRV_DEPRESSION, 2800))
    assert rec == "maintenance"
    assert "swap HIIT for Z2" in action["training"]


def test_engine_kcal_override_propagates():
    """If the engine ever changes kcal targets, the adapter follows."""
    rec, _, action = map_to_legacy(_call(RegulationState.DEFICIT, 2200))
    assert action["kcal"] == 2200  # not 2300 default
