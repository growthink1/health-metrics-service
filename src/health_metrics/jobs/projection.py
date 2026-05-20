"""Bayesian projection models for v4 goal tracking.

Closed-form conjugate updates. No PyMC.
- Continuous metrics (weight, strength, HRV) -> Normal-Normal on slope (or % gain).
- Discrete habit metric -> Beta-Binomial on per-day probability.
"""

from datetime import date as date_type
from typing import Any, Literal

import numpy as np
from scipy import stats

# Prior parameters per spec section 2
PRIOR_WEIGHT_LOSS = {"mu": -0.5 / 7, "sigma": 0.5 / 7}  # per-day units
PRIOR_WEIGHT_GAIN = {"mu": 0.25 / 7, "sigma": 0.5 / 7}
PRIOR_STRENGTH_PCT_PER_WK = {"mu": 0.01, "sigma": 0.0075}
PRIOR_HABIT_BETA = (4.0, 3.0)
PRIOR_HRV_PER_WK = {"mu": 0.5, "sigma": 1.5}

MIN_OBS_WEIGHT = 7
MIN_OBS_STRENGTH = 4
MIN_OBS_HABIT_DAYS = 7
MIN_OBS_HRV = 30


def _insufficient(n: int, min_required: int, current_value: float | None) -> dict:
    return {
        "method": "insufficient_data",
        "current_value": current_value,
        "projected_value_mean": None,
        "projected_value_ci_low": None,
        "projected_value_ci_high": None,
        "p_on_pace": None,
        "confidence": "low",
        "data_points_used": n,
        "min_required": min_required,
    }


def _confidence_from_ci(ci_low: float, ci_high: float, gap: float, n: int, min_n: int) -> str:
    if n < int(min_n * 1.5):
        return "low"
    if gap <= 0:
        return "high"
    width = abs(ci_high - ci_low)
    ratio = width / abs(gap)
    if ratio < 0.20:
        return "high"
    if ratio < 0.50:
        return "med"
    return "low"


def _normal_normal_posterior(prior_mu, prior_sigma, mle, mle_se, mle_se_floor=1e-6):
    """Conjugate Normal-Normal update on slope-like parameter.

    If mle_se is below ``mle_se_floor`` (e.g., perfectly-linear synthetic data
    or scipy.linregress floating-point noise), fall back to the prior rather
    than letting astronomical data-precision overwhelm it.
    """
    if mle_se <= mle_se_floor or not np.isfinite(mle_se):
        return prior_mu, prior_sigma
    prior_prec = 1.0 / (prior_sigma**2)
    data_prec = 1.0 / (mle_se**2)
    post_prec = prior_prec + data_prec
    post_var = 1.0 / post_prec
    post_mu = (prior_mu * prior_prec + mle * data_prec) / post_prec
    return post_mu, post_var**0.5


def project_weight(
    observations: list[tuple[date_type, float]],
    current_date: date_type,
    target_value: float,
    target_date: date_type,
    goal_direction: Literal["down", "up", "hold"],
) -> dict[str, Any]:
    """Project weight at target_date from last 30d observations."""
    obs = [(d, v) for d, v in observations if (current_date - d).days <= 30]
    n = len(obs)
    if n == 0:
        return _insufficient(0, MIN_OBS_WEIGHT, None)
    current_value = obs[-1][1]
    if n < MIN_OBS_WEIGHT:
        return _insufficient(n, MIN_OBS_WEIGHT, current_value)

    # OLS slope per day
    xs = np.array([(d - obs[0][0]).days for d, _ in obs], dtype=float)
    ys = np.array([v for _, v in obs], dtype=float)
    slope, intercept, _, _, slope_se = stats.linregress(xs, ys)

    prior = PRIOR_WEIGHT_LOSS if goal_direction == "down" else PRIOR_WEIGHT_GAIN
    post_mu, post_sigma = _normal_normal_posterior(prior["mu"], prior["sigma"], slope, slope_se)

    days_remaining = (target_date - current_date).days
    proj_mean = current_value + post_mu * days_remaining
    proj_std = post_sigma * abs(days_remaining)
    ci_low, ci_high = proj_mean - 1.96 * proj_std, proj_mean + 1.96 * proj_std

    # P(value_at_deadline <= target_value) for loss, >= for gain
    z = (target_value - proj_mean) / max(proj_std, 1e-6)
    p_on_pace = (
        float(stats.norm.cdf(z)) if goal_direction == "down" else float(1 - stats.norm.cdf(z))
    )

    gap = abs(target_value - current_value)
    return {
        "method": "bayesian_normal_normal",
        "current_value": float(current_value),
        "projected_value_mean": float(proj_mean),
        "projected_value_ci_low": float(ci_low),
        "projected_value_ci_high": float(ci_high),
        "p_on_pace": float(p_on_pace),
        "confidence": _confidence_from_ci(ci_low, ci_high, gap, n, MIN_OBS_WEIGHT),
        "data_points_used": n,
        # Report posterior in lb/wk (post_mu is per-day) so caller sees a familiar weekly rate.
        "posterior_params": {
            "weekly_slope_mean": float(post_mu * 7),
            "weekly_slope_std": float(post_sigma * 7),
        },
    }


def project_strength(
    pr_observations: list[tuple[date_type, float]],
    current_date: date_type,
    target_value: float,
    target_date: date_type,
) -> dict[str, Any]:
    """Project best lift at target reps at target_date from last 60d of PR observations."""
    obs = [(d, v) for d, v in pr_observations if (current_date - d).days <= 60]
    n = len(obs)
    current_value = obs[-1][1] if obs else None
    if n < MIN_OBS_STRENGTH:
        return _insufficient(n, MIN_OBS_STRENGTH, current_value)
    if obs and obs[0][1] <= 0:
        # Zero/negative starting PR would make log(values / obs[0][1]) infinite/NaN.
        return _insufficient(n, MIN_OBS_STRENGTH, current_value)

    # weekly % gain regression
    weeks = np.array([(d - obs[0][0]).days / 7.0 for d, _ in obs])
    pct = np.log(np.array([v for _, v in obs], dtype=float) / obs[0][1])
    slope, _, _, _, slope_se = stats.linregress(weeks, pct)
    prior = PRIOR_STRENGTH_PCT_PER_WK
    post_mu, post_sigma = _normal_normal_posterior(prior["mu"], prior["sigma"], slope, slope_se)

    weeks_remaining = (target_date - current_date).days / 7.0
    proj_mean = current_value * float(np.exp(post_mu * weeks_remaining))
    proj_std = abs(current_value * post_sigma * weeks_remaining)
    ci_low, ci_high = proj_mean - 1.96 * proj_std, proj_mean + 1.96 * proj_std

    z = (proj_mean - target_value) / max(proj_std, 1e-6)
    p_on_pace = float(stats.norm.cdf(z))
    gap = abs(target_value - current_value)
    return {
        "method": "bayesian_normal_normal",
        "current_value": float(current_value),
        "projected_value_mean": float(proj_mean),
        "projected_value_ci_low": float(ci_low),
        "projected_value_ci_high": float(ci_high),
        "p_on_pace": p_on_pace,
        "confidence": _confidence_from_ci(ci_low, ci_high, gap, n, MIN_OBS_STRENGTH),
        "data_points_used": n,
        "posterior_params": {
            "weekly_pct_gain_mean": float(post_mu),
            "weekly_pct_gain_std": float(post_sigma),
        },
    }


def project_habit(
    day_workouts: list[tuple[date_type, bool]],
    current_date: date_type,
    target_value: float,
) -> dict[str, Any]:
    """Project workouts-per-week from last 28d Bernoulli trials."""
    obs = [(d, b) for d, b in day_workouts if 0 <= (current_date - d).days <= 28]
    n = len(obs)
    if n < MIN_OBS_HABIT_DAYS:
        cur = sum(int(b) for _, b in obs) * (7 / max(n, 1)) if obs else None
        return _insufficient(n, MIN_OBS_HABIT_DAYS, cur)

    k = sum(int(b) for _, b in obs)
    alpha, beta = PRIOR_HABIT_BETA
    post_alpha, post_beta = alpha + k, beta + (n - k)
    mean_p = post_alpha / (post_alpha + post_beta)
    proj_mean = 7.0 * mean_p
    ci_low_p = float(stats.beta.ppf(0.025, post_alpha, post_beta))
    ci_high_p = float(stats.beta.ppf(0.975, post_alpha, post_beta))
    ci_low, ci_high = 7.0 * ci_low_p, 7.0 * ci_high_p
    # Current rate over last 7d
    last_7 = [b for d, b in obs if (current_date - d).days < 7]
    current_value = float(sum(int(b) for b in last_7))
    p_on_pace = float(1 - stats.beta.cdf((target_value / 7.0), post_alpha, post_beta))
    gap = abs(target_value - current_value)
    return {
        "method": "beta_binomial",
        "current_value": current_value,
        "projected_value_mean": float(proj_mean),
        "projected_value_ci_low": float(ci_low),
        "projected_value_ci_high": float(ci_high),
        "p_on_pace": p_on_pace,
        "confidence": _confidence_from_ci(ci_low, ci_high, max(gap, 1.0), n, MIN_OBS_HABIT_DAYS),
        "data_points_used": n,
        "posterior_params": {"alpha": float(post_alpha), "beta": float(post_beta)},
    }


def project_hrv(
    daily_hrv: list[tuple[date_type, float]],
    current_date: date_type,
    target_value: float,
    target_date: date_type,
) -> dict[str, Any]:
    """Project 30-day-rolling-avg HRV at target_date from last 60d data."""
    obs = [(d, v) for d, v in daily_hrv if 0 <= (current_date - d).days <= 60]
    n = len(obs)
    if n < MIN_OBS_HRV:
        cur = obs[-1][1] if obs else None
        return _insufficient(n, MIN_OBS_HRV, cur)

    # Compute rolling 7d avg, then regress weekly slope
    obs.sort(key=lambda x: x[0])
    values = np.array([v for _, v in obs], dtype=float)
    if len(values) >= 7:
        rolled = np.convolve(values, np.ones(7) / 7, mode="valid")
    else:
        rolled = values
    weeks = np.arange(len(rolled)) / 7.0
    slope, _, _, _, slope_se = stats.linregress(weeks, rolled)
    prior = PRIOR_HRV_PER_WK
    post_mu, post_sigma = _normal_normal_posterior(prior["mu"], prior["sigma"], slope, slope_se)

    weeks_remaining = (target_date - current_date).days / 7.0
    current_value = float(rolled[-1])
    proj_mean = current_value + post_mu * weeks_remaining
    proj_std = abs(post_sigma * weeks_remaining)
    ci_low, ci_high = proj_mean - 1.96 * proj_std, proj_mean + 1.96 * proj_std

    z = (proj_mean - target_value) / max(proj_std, 1e-6)
    p_on_pace = float(stats.norm.cdf(z))
    gap = abs(target_value - current_value)
    return {
        "method": "bayesian_normal_normal",
        "current_value": current_value,
        "projected_value_mean": float(proj_mean),
        "projected_value_ci_low": float(ci_low),
        "projected_value_ci_high": float(ci_high),
        "p_on_pace": p_on_pace,
        "confidence": _confidence_from_ci(ci_low, ci_high, gap, n, MIN_OBS_HRV),
        "data_points_used": n,
        "posterior_params": {
            "weekly_slope_mean": float(post_mu),
            "weekly_slope_std": float(post_sigma),
        },
    }
