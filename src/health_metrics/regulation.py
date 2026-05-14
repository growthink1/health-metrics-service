"""Auto-regulation decision rules.

Mirrors the deterministic decision tree from docs/spec.md §"Auto-regulation
logic". Conservative bias — when ambiguous, prefer the safer recommendation.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class RegulationSignals:
    hrv_z_3d: float                       # avg z-score over last 3 days vs 14d baseline
    rhr_z_3d: float
    sleep_3d_min: float                   # avg minutes over last 3 days
    sleep_debt_min: float                 # from Whoop (positive = behind)
    strain_7d_total: float
    subjective_3d_energy: float | None    # 1-10, None if missing
    days_with_complete_data: int          # of last 3, how many ingestion_complete


RecType = Literal["deficit", "deficit_conservative", "maintenance", "deload"]


def regulate(s: RegulationSignals) -> tuple[RecType, list[str], dict]:
    """Return (recommendation, rationale_list, action_payload)."""
    rationale: list[str] = []

    recovery_score = (
        -0.50 * s.hrv_z_3d
        - 0.30 * s.rhr_z_3d
        + 0.40 * ((s.sleep_3d_min - 360) / 60)
    )

    # Hard floor: severe sleep deprivation
    if s.sleep_3d_min < 300:
        rationale.append(f"Severe sleep debt: {s.sleep_3d_min / 60:.1f}h avg over 3d")
        return (
            "deload",
            rationale,
            {"kcal": 2800, "training": "Volume -30%, Z2 only, extra rest day"},
        )

    # Hard floor: subjective collapse (if logged)
    if s.subjective_3d_energy is not None and s.subjective_3d_energy < 4:
        rationale.append(
            f"Subjective energy collapsed: {s.subjective_3d_energy:.1f}/10"
        )
        return (
            "deload",
            rationale,
            {"kcal": 2800, "training": "Volume -30%, Z2 only, extra rest day"},
        )

    # Severe recovery + sleep compromise
    if recovery_score < -1.0 and s.sleep_3d_min < 360:
        rationale.append(
            f"Recovery composite {recovery_score:.2f} + sleep {s.sleep_3d_min / 60:.1f}h"
        )
        return (
            "deload",
            rationale,
            {"kcal": 2800, "training": "Volume -30%, swap HIIT for Z2"},
        )

    # Mild recovery compromise — pause deficit, train normally
    if recovery_score < -0.5 or s.sleep_3d_min < 390:
        rationale.append(
            f"Recovery markers depressed (score {recovery_score:.2f}, "
            f"sleep {s.sleep_3d_min / 60:.1f}h)"
        )
        return (
            "maintenance",
            rationale,
            {"kcal": 2800, "training": "Full program, no progression push"},
        )

    # Excessive strain accumulation
    if s.strain_7d_total / 7 > 15:
        rationale.append(
            f"7d strain load high: {s.strain_7d_total:.1f} "
            f"({s.strain_7d_total / 7:.1f}/day avg)"
        )
        return (
            "deficit_conservative",
            rationale,
            {"kcal": 2500, "training": "Full program, monitor closely"},
        )

    # All clear
    if recovery_score > 0 and s.strain_7d_total / 7 < 13:
        rationale.append(
            f"All signals green: recovery {recovery_score:.2f}, "
            f"strain {s.strain_7d_total / 7:.1f}/d"
        )
        return (
            "deficit",
            rationale,
            {"kcal": 2300, "training": "Full program, progression OK"},
        )

    # Conservative bias default
    rationale.append(
        f"Mixed signals (recovery {recovery_score:.2f}, "
        f"strain {s.strain_7d_total / 7:.1f}/d) — conservative"
    )
    return (
        "deficit_conservative",
        rationale,
        {"kcal": 2500, "training": "Full program, monitor closely"},
    )
