"""Offline validation of the training-water priors against the held-out Jun 20–26
episode. NOT in the request path. Usage:

    .venv/bin/python scripts/validate_water_retention.py
"""

import json
from datetime import date
from pathlib import Path

from health_metrics.regulation.water_retention import (
    WaterRetentionParams,
    training_water_series,
)
from health_metrics.regulation.water_retention_config import fallback_load, get_water_params

FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "days" / "glycogen-real-hugo-2026-06-26.json"
EPISODE = [date(2026, 6, d) for d in range(20, 27)]  # held-out window


def _load():
    data = json.loads(FIXTURE.read_text())
    weights: dict[date, float] = {}
    loads: dict[date, float] = {}
    for row in data["series"]:
        d = date.fromisoformat(row["date"])
        if row["weight_lbs"] is not None:
            weights[d] = float(row["weight_lbs"])
        for w in row.get("workouts", []):
            strain = float(w["strain"]) if w["strain"] is not None else None
            loads[d] = loads.get(d, 0.0) + fallback_load(w["type"], strain)
    return weights, loads


def _max_day_over_day_increase(series: list[float]) -> float:
    return max((series[i + 1] - series[i] for i in range(len(series) - 1)), default=0.0)


def _evaluate(params: WaterRetentionParams, weights, loads):
    ep_dates = [d for d in EPISODE if d in weights]
    raw = [weights[d] for d in ep_dates]
    water = training_water_series(loads, ep_dates, params)
    offset = {p.date: p.offset_lbs for p in water}
    dewatered = [weights[d] - offset[d] for d in ep_dates]
    raw_inc = _max_day_over_day_increase(raw)
    dw_inc = _max_day_over_day_increase(dewatered)
    return ep_dates, raw, dewatered, raw_inc, dw_inc


def main() -> None:
    weights, loads = _load()
    params = get_water_params("hugo")
    ep_dates, raw, dewatered, raw_inc, dw_inc = _evaluate(params, weights, loads)

    print(f"Priors: k={params.k}, lam={params.lam}")
    print(f"{'date':<12}{'raw':>8}{'dewatered':>12}")
    for d, r, dw in zip(ep_dates, raw, dewatered, strict=True):
        print(f"{d.isoformat():<12}{r:>8.1f}{dw:>12.2f}")
    print(f"\nRaw max day-over-day increase:       {raw_inc:.2f} lb")
    print(f"De-watered max day-over-day increase: {dw_inc:.2f} lb")
    passed = dw_inc < raw_inc
    print(f"\nPriors {'PASS' if passed else 'FAIL'} (de-watered increase < raw)")
    if not passed:
        print("→ Priors did not de-water the episode. Next step: bounded grid")
        print("   k in [0.02,0.08] step 0.01, lam in [0.2,0.6] step 0.05;")
        print("   pick the gentlest point with dw_inc < raw_inc. If none clears,")
        print("   ship annotation-only (runtime gate auto-disables de-watering).")


if __name__ == "__main__":
    main()
