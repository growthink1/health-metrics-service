"""Katch-McArdle RMR from lean body mass. Pure — no DB, no network.

RMR_kcal = 370 + 21.6 * lean_mass_kg
"""

from __future__ import annotations

LBS_PER_KG = 2.20462


def katch_mcardle_rmr(lean_mass_lbs: float) -> int:
    lean_mass_kg = lean_mass_lbs / LBS_PER_KG
    return round(370 + 21.6 * lean_mass_kg)
