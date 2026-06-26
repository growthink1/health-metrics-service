"""Offline glycogen param fit. NOT in the request path.

Usage: .venv/bin/python scripts/fit_glycogen_params.py
"""

import json
from datetime import date
from pathlib import Path

from health_metrics.regulation.glycogen import DayPoint, fit_params

FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "days" / "glycogen-real-hugo-2026-06-26.json"


def _load() -> list[DayPoint]:
    data = json.loads(FIXTURE.read_text())
    out = []
    for row in data["series"]:
        d = date.fromisoformat(row["date"])
        workouts = [
            (w["type"], float(w["strain"]) if w["strain"] is not None else 1.0) for w in row.get("workouts", [])
        ]
        out.append(
            DayPoint(
                date=d,
                weight_lbs=row["weight_lbs"],
                carbs_g=row["carbs_g"],
                workouts=workouts,
            )
        )
    return out


def main() -> None:
    series = _load()
    holdout = {date(2026, 6, d) for d in range(20, 27)}  # Jun 20-26 out-of-sample
    params, fit_resid, hold_resid = fit_params(series, holdout_dates=holdout)
    print(f"Fitted params: {params}")
    print(f"Fit-window residual:     {fit_resid:.4f}")
    print(f"Holdout-window residual: {hold_resid:.4f}")
    print(f"Overfit ratio (hold/fit): {hold_resid / fit_resid if fit_resid else float('inf'):.2f}  (want <1.5)")
    print()
    print("Paste into glycogen_config.py:")
    print('    "hugo": GlycogenParams(')
    print(f"        alpha={params.alpha:.4f}, carb_maintenance={params.carb_maintenance:.1f}, beta={params.beta:.4f},")
    print(f"        g_min={params.g_min:.1f}, g_max={params.g_max:.1f}, tier_scale={params.tier_scale:.4f},")
    print("    ),")


if __name__ == "__main__":
    main()
