"""Daily energy model: dedup activity, NEAT term, measured/modeled blend.

Pure — no DB, no network, no clock. The brief layer (compute_energy_today in
brief.py) supplies RMR, activities, and the whoop_complete flag. See
docs/superpowers/specs/2026-07-13-activity-neat-energy-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from .energy_config import PER_TYPE_KCAL_PER_MIN, EnergyParams
from .schemas import EnergyToday


@dataclass(frozen=True)
class Activity:
    activity_type: str  # normalized enum value
    source_layer: str  # "manual" | "auto"
    distance_mi: float | None
    duration_min: int | None
    kcal: float | None


def dedup_activities(activities: list[Activity]) -> list[Activity]:
    """Drop auto entries whose type also appears in a manual entry (manual wins)."""
    manual_types = {a.activity_type for a in activities if a.source_layer == "manual"}
    return [a for a in activities if not (a.source_layer == "auto" and a.activity_type in manual_types)]


def activity_neat_kcal(a: Activity, weight_lbs: float | None, params: EnergyParams) -> float:
    if a.kcal is not None:
        return a.kcal
    if a.distance_mi is not None and weight_lbs is not None and a.activity_type in ("walk", "run"):
        return a.distance_mi * weight_lbs * params.neat_coef
    if a.duration_min is not None:
        return a.duration_min * PER_TYPE_KCAL_PER_MIN.get(a.activity_type, PER_TYPE_KCAL_PER_MIN["other"])
    return 0.0


def neat_kcal(activities: list[Activity], weight_lbs: float | None, params: EnergyParams) -> float:
    return sum(activity_neat_kcal(a, weight_lbs, params) for a in dedup_activities(activities))


def activity_label(a: Activity) -> str:
    layer = "activity_log" if a.source_layer == "manual" else "workouts"
    if a.distance_mi is not None:
        return f"{a.activity_type} {a.distance_mi}mi ({layer})"
    if a.duration_min is not None:
        return f"{a.activity_type} {a.duration_min}min ({layer})"
    return f"{a.activity_type} ({layer})"


def compute_energy(
    rmr_kcal: int,
    rmr_source: str,
    weight_lbs: float | None,
    activities: list[Activity],
    whoop_kcal_burned: int | None,
    whoop_complete: bool,
    params: EnergyParams,
) -> EnergyToday:
    deduped = dedup_activities(activities)
    neat = round(sum(activity_neat_kcal(a, weight_lbs, params) for a in deduped), 1)
    baseline = round(rmr_kcal * params.baseline_activity_factor)
    modeled = baseline + round(neat)
    measured = whoop_kcal_burned if whoop_complete else None

    divergence = False
    if measured is not None and modeled > 0:
        divergence = abs(measured - modeled) / modeled > params.divergence_pct

    return EnergyToday(
        neat_kcal=neat,
        baseline_kcal=baseline,
        rmr_kcal=rmr_kcal,
        tdee_measured_kcal=measured,
        tdee_modeled_kcal=modeled,
        tdee_estimate_kcal=modeled,  # headline = modeled (calibrated)
        divergence_flag=divergence,
        activities_counted=[activity_label(a) for a in deduped],
        rmr_source="dexa" if rmr_source == "dexa" else "fallback",
    )
