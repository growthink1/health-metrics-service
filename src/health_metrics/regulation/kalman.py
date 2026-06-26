"""1-D Kalman filter for de-watering bodyweight readings.

Constant-velocity (local linear trend) state model -- we get both smoothed level
AND velocity. Velocity x 3500 kcal/lb = daily energy deficit -> revealed TDEE.

Pure function -- no I/O, no DB. Inputs: list of (date, weight | None) observations
in chronological order. Outputs: list of FilteredPoint (level, velocity, level_var,
velocity_var) per observation date.

Missing days (weight=None or gap in date sequence): predict step only (no update).
The filter remains numerically stable across gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta

import numpy as np

# Process noise (tune via tests on real history):
#   q_level governs how fast true weight drifts day-over-day (small -- true weight
#   changes slowly relative to noise).
#   q_vel governs how fast the velocity itself can change (very small -- fat-loss
#   rate evolves on a weeks-to-months timescale).
_Q_LEVEL = 0.01
_Q_VEL = 0.0005

# Observation noise:
#   r_obs is the daily scale reading variance from glycogen/water/food.
#   Empirically +-1-1.5 lb 1-sigma; we pick 1.0 lb^2 (1.0 lb sigma) as the prior.
_R_OBS = 1.0

# Initial uncertainty:
_P0_LEVEL = 2.0
_P0_VEL = 0.1


@dataclass(frozen=True)
class FilteredPoint:
    date: date_type
    level: float  # smoothed weight estimate (lb)
    velocity: float  # smoothed rate of change (lb/day)
    level_var: float  # 1-sigma uncertainty on level (lb^2)
    velocity_var: float  # 1-sigma uncertainty on velocity ((lb/day)^2)
    is_observed: bool  # True if this date had an actual scale reading


def kalman_weight(
    observations: list[tuple[date_type, float | None]],
) -> list[FilteredPoint]:
    """Filter a sequence of (date, weight) pairs. Returns a FilteredPoint per
    date in the input (including missing-data dates -- those use predict-only).

    Caller is responsible for ordering observations chronologically. If the
    sequence has gaps (missing dates), the filter walks day-by-day from the
    first to the last date and emits a FilteredPoint per day; the input
    observations are matched by date.

    Returns [] for empty input or input with no observed (non-None) values.
    """
    if not observations:
        return []

    # Order observations by date + index for fast lookup
    by_date: dict[date_type, float] = {}
    for d, w in observations:
        if w is not None:
            by_date[d] = float(w)

    first_date = observations[0][0]
    last_date = observations[-1][0]
    # Find the first observed day to initialize
    first_obs = next((w for _, w in observations if w is not None), None)
    if first_obs is None:
        return []

    # State + covariance
    x = np.array([first_obs, 0.0], dtype=float)
    P = np.diag([_P0_LEVEL, _P0_VEL]).astype(float)
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    Q = np.diag([_Q_LEVEL, _Q_VEL])
    H = np.array([[1.0, 0.0]])
    R = np.array([[_R_OBS]])

    out: list[FilteredPoint] = []
    cur = first_date
    while cur <= last_date:
        # Predict
        x = F @ x
        P = F @ P @ F.T + Q
        # Update only if observed
        if cur in by_date:
            y = np.array([by_date[cur]]) - H @ x
            S = H @ P @ H.T + R
            K = P @ H.T @ np.linalg.inv(S)
            x = x + (K @ y).flatten()
            P = (np.eye(2) - K @ H) @ P
            observed = True
        else:
            observed = False
        out.append(
            FilteredPoint(
                date=cur,
                level=float(x[0]),
                velocity=float(x[1]),
                level_var=float(P[0, 0]),
                velocity_var=float(P[1, 1]),
                is_observed=observed,
            )
        )
        cur += timedelta(days=1)

    return out
