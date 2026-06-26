"""Per-user training-water params + per-type strain fallback.

Hugo's (k, lam) are physiological priors validated offline against the held-out
Jun 20–26 episode (scripts/validate_water_retention.py). Re-validate after more
data accumulates. Andrea ships the same defaults — flagged for her own validation.
"""

from .water_retention import WaterRetentionParams

# k=0.05 → strain-14 session ≈ 0.7 lb; lam=0.3466 → ~2-day half-life.
_DEFAULT = WaterRetentionParams(k=0.05, lam=0.3466)

_PARAMS_BY_USER: dict[str, WaterRetentionParams] = {
    "hugo": WaterRetentionParams(k=0.05, lam=0.3466),
    # TODO: validate Andrea's params once she has >=3 weeks of weight+workout logs.
    "andrea": WaterRetentionParams(k=0.05, lam=0.3466),
}

# Assumed strain when a workout row has strain=NULL, by workout_type.
STRAIN_FALLBACK: dict[str, float] = {
    "functional-fitness": 12.0,
    "cycling": 11.0,
    "walking": 5.0,
    "activity": 8.0,
    "yard-work": 6.0,
    "weightlifting": 10.0,
    "weightlifting_msk": 10.0,
}
_DEFAULT_STRAIN = 8.0


def get_water_params(user_id: str) -> WaterRetentionParams:
    return _PARAMS_BY_USER.get(user_id, _DEFAULT)


def fallback_load(workout_type: str | None, strain: float | None) -> float:
    if strain is not None:
        return strain
    if workout_type is None:
        return _DEFAULT_STRAIN
    return STRAIN_FALLBACK.get(workout_type, _DEFAULT_STRAIN)
