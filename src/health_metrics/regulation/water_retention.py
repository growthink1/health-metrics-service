"""Training-induced water retention kernel (supersedes the glycogen Phase 2 model).

Each workout deposits a water bolus that decays exponentially:

    water(t) = Σ_{sessions s, day_s ≤ t}  k · load_s · e^(−lam · (t − day_s))

Pure functions — no DB, no network. Params are physiological priors validated
offline (scripts/validate_water_retention.py); they live in
water_retention_config.py. See docs/superpowers/specs/2026-06-26-training-water-retention-design.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta


@dataclass(frozen=True)
class WaterRetentionParams:
    k: float  # gain, lb per load-unit
    lam: float  # decay rate, per day (half-life = ln2 / lam)


@dataclass(frozen=True)
class DayWater:
    date: date_type
    water_lbs: float  # absolute Σ bolus on this day
    offset_lbs: float  # deviation from the window mean (what gets subtracted)


def _absolute_water(
    target: date_type,
    loads_by_day: dict[date_type, float],
    params: WaterRetentionParams,
) -> float:
    total = 0.0
    for day, load in loads_by_day.items():
        days_since = (target - day).days
        if days_since < 0:
            continue  # future sessions don't affect the past
        total += params.k * load * math.exp(-params.lam * days_since)
    return total


def training_water_series(
    loads_by_day: dict[date_type, float],
    dates: list[date_type],
    params: WaterRetentionParams,
) -> list[DayWater]:
    """One DayWater per date in `dates`. offset_lbs is the deviation from the
    window-mean absolute water (we subtract the swing, not the level)."""
    abs_water = [_absolute_water(d, loads_by_day, params) for d in dates]
    baseline = sum(abs_water) / len(abs_water) if abs_water else 0.0
    return [DayWater(date=d, water_lbs=w, offset_lbs=w - baseline) for d, w in zip(dates, abs_water, strict=True)]


def clears_by(
    today: date_type,
    loads_by_day: dict[date_type, float],
    params: WaterRetentionParams,
    threshold: float = 0.2,
    horizon: int = 14,
) -> date_type | None:
    """First future date (today..today+horizon) where projected absolute water
    drops below `threshold`. None if already below today, or never within horizon."""
    if _absolute_water(today, loads_by_day, params) < threshold:
        return None
    for i in range(1, horizon + 1):
        d = today + timedelta(days=i)
        if _absolute_water(d, loads_by_day, params) < threshold:
            return d
    return None
