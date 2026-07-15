"""Per-user energy-model constants + activity-type maps.

Constants are SEED values pending offline calibration
(scripts/calibrate_energy.py) against whoop_kcal_burned + Kalman revealed_tdee.
Re-run and update after body composition shifts. See
docs/superpowers/specs/2026-07-13-activity-neat-energy-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnergyParams:
    baseline_activity_factor: float  # sedentary desk multiplier on RMR (~3k steps)
    neat_coef: float  # net-of-resting kcal per mile per lb, for distance walks/runs
    fallback_rmr_kcal: int  # used only when no body_composition row exists
    divergence_pct: float  # |measured-modeled|/modeled threshold for the flag


# SEED constants (pre-calibration). baseline 1.35 = sedentary; walks add on top.
_DEFAULT = EnergyParams(baseline_activity_factor=1.35, neat_coef=0.53, fallback_rmr_kcal=2000, divergence_pct=0.10)

_PARAMS_BY_USER: dict[str, EnergyParams] = {
    "hugo": _DEFAULT,
    "andrea": _DEFAULT,
}

# workouts.workout_type -> activity_type enum value
NORMALIZE_TYPE: dict[str, str] = {
    "walking": "walk",
    "walk": "walk",
    "running": "run",
    "run": "run",
    "cycling": "ride",
    "ride": "ride",
    "functional-fitness": "strength",
    "weightlifting": "strength",
    "strength": "strength",
    "climbing": "climb",
    "climb": "climb",
    "hiit": "hiit",
    "z2": "z2",
}

# Duration-only fallback kcal/min above resting, by normalized activity_type.
PER_TYPE_KCAL_PER_MIN: dict[str, float] = {
    "walk": 3.0,
    "run": 8.0,
    "ride": 6.0,
    "z2": 6.0,
    "hiit": 9.0,
    "strength": 4.0,
    "climb": 7.0,
    "other": 4.0,
}


def get_energy_params(user_id: str) -> EnergyParams:
    return _PARAMS_BY_USER.get(user_id, _DEFAULT)


def normalize_activity_type(raw: str | None) -> str:
    if raw is None:
        return "other"
    return NORMALIZE_TYPE.get(raw, raw if raw in PER_TYPE_KCAL_PER_MIN else "other")
