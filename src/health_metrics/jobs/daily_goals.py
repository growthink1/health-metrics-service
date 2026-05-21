"""Daily per-goal recompute: trajectory → actions → cached narration."""

import hashlib
import json
from datetime import date as date_type, datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import (DailyMetrics, Goal, GoalRecommendation, ManualLog, Meal,
                       Milestone, Subgoal, Workout)
from ..regulation import compute_regulation_signals, regulate
from .projection import project_habit, project_hrv, project_strength, project_weight

log = structlog.get_logger()


# ---- direction inference (shared helper) -----------------------------------

def _goal_direction_down(goal: Goal, current_value: float | None) -> bool:
    """True if goal is a 'down' goal (target < start).

    Uses ``current_value`` as the baseline when ``start_value`` is None — handles
    lazy-create goals where start_value is backfilled after the first
    ``compute_current_value`` call. If neither is available, falls back to a
    baseline just above the target so the default direction is "down" (the
    common case for weight-loss goals). Conservative.
    """
    if goal.start_value is not None:
        baseline = float(goal.start_value)
    elif current_value is not None:
        baseline = current_value
    else:
        baseline = float(goal.target_value) + 1.0
    return float(goal.target_value) < baseline


# ---- per-goal-type current-value computation --------------------------------

async def compute_current_value(session: AsyncSession, goal: Goal, anchor: date_type) -> float | None:
    if goal.goal_type == "weight":
        res = await session.execute(
            select(ManualLog.weight_lbs).where(
                ManualLog.user_id == goal.user_id,
                ManualLog.weight_lbs.is_not(None),
                ManualLog.log_date <= anchor,
            ).order_by(ManualLog.log_date.desc()).limit(1)
        )
        v = res.scalar_one_or_none()
        return float(v) if v is not None else None
    if goal.goal_type == "strength":
        params = goal.metric_params or {}
        exercise = params.get("exercise", "back squat")
        reps = int(params.get("reps", 5))
        # need WorkoutSet model; query best weight at >= reps in the last 60 days
        from ..models import WorkoutSet
        cutoff = anchor - timedelta(days=60)
        res = await session.execute(
            select(func.max(WorkoutSet.weight_lbs)).where(
                WorkoutSet.user_id == goal.user_id,
                WorkoutSet.exercise == exercise,
                WorkoutSet.reps >= reps,
                func.date(WorkoutSet.created_at) >= cutoff,
            )
        )
        v = res.scalar_one_or_none()
        return float(v) if v is not None else None
    if goal.goal_type == "habit":
        cutoff = anchor - timedelta(days=7)
        res = await session.execute(
            select(func.count()).select_from(Workout).where(
                Workout.user_id == goal.user_id,
                Workout.workout_date >= cutoff,
                Workout.workout_date <= anchor,
            )
        )
        return float(res.scalar() or 0)
    if goal.goal_type == "recovery_hrv":
        cutoff = anchor - timedelta(days=30)
        res = await session.execute(
            select(func.avg(DailyMetrics.oura_hrv_avg)).where(
                DailyMetrics.user_id == goal.user_id,
                DailyMetrics.metric_date >= cutoff,
                DailyMetrics.metric_date <= anchor,
            )
        )
        v = res.scalar_one_or_none()
        return float(v) if v is not None else None
    return None


# ---- per-goal-type projection ----------------------------------------------

async def project_to_deadline(
    session: AsyncSession,
    goal: Goal,
    anchor: date_type,
    current_value: float | None = None,
) -> dict[str, Any]:
    if goal.goal_type == "weight":
        res = await session.execute(
            select(ManualLog.log_date, ManualLog.weight_lbs).where(
                ManualLog.user_id == goal.user_id,
                ManualLog.weight_lbs.is_not(None),
                ManualLog.log_date <= anchor,
                ManualLog.log_date >= anchor - timedelta(days=30),
            ).order_by(ManualLog.log_date.asc())
        )
        obs = [(d, float(v)) for d, v in res.all()]
        # Use caller-supplied current_value if provided (avoids a duplicate query
        # when the orchestrator already computed it). Otherwise fetch it here so
        # _goal_direction_down has a baseline when start_value is None.
        cv_for_direction = (
            current_value if current_value is not None
            else await compute_current_value(session, goal, anchor)
        )
        direction = "down" if _goal_direction_down(goal, cv_for_direction) else "up"
        return project_weight(obs, anchor, float(goal.target_value), goal.target_date, direction)

    if goal.goal_type == "strength":
        from ..models import WorkoutSet
        params = goal.metric_params or {}
        exercise = params.get("exercise", "back squat")
        reps = int(params.get("reps", 5))
        res = await session.execute(
            select(func.date(WorkoutSet.created_at), func.max(WorkoutSet.weight_lbs)).where(
                WorkoutSet.user_id == goal.user_id,
                WorkoutSet.exercise == exercise,
                WorkoutSet.reps >= reps,
                func.date(WorkoutSet.created_at) >= anchor - timedelta(days=60),
            ).group_by(func.date(WorkoutSet.created_at)).order_by(func.date(WorkoutSet.created_at).asc())
        )
        pr_obs = [(d, float(v)) for d, v in res.all()]
        return project_strength(pr_obs, anchor, float(goal.target_value), goal.target_date)

    if goal.goal_type == "habit":
        cutoff = anchor - timedelta(days=28)
        res = await session.execute(
            select(Workout.workout_date).where(
                Workout.user_id == goal.user_id,
                Workout.workout_date >= cutoff,
                Workout.workout_date <= anchor,
            )
        )
        days_with = {d for (d,) in res.all()}
        obs = [(cutoff + timedelta(days=i), (cutoff + timedelta(days=i)) in days_with)
               for i in range(28)]
        return project_habit(obs, anchor, float(goal.target_value))

    if goal.goal_type == "recovery_hrv":
        res = await session.execute(
            select(DailyMetrics.metric_date, DailyMetrics.oura_hrv_avg).where(
                DailyMetrics.user_id == goal.user_id,
                DailyMetrics.oura_hrv_avg.is_not(None),
                DailyMetrics.metric_date >= anchor - timedelta(days=60),
                DailyMetrics.metric_date <= anchor,
            ).order_by(DailyMetrics.metric_date.asc())
        )
        obs = [(d, float(v)) for d, v in res.all()]
        return project_hrv(obs, anchor, float(goal.target_value), goal.target_date)

    return {"method": "insufficient_data", "current_value": None, "data_points_used": 0,
            "min_required": 0, "projected_value_mean": None, "projected_value_ci_low": None,
            "projected_value_ci_high": None, "p_on_pace": None, "confidence": "low"}


# ---- subgoal compliance -----------------------------------------------------

_SUBGOAL_DISPATCH: dict[str, str] = {
    "avg_kcal": "kcal_consumed",
    "protein_g_avg": "protein_g",
    "sleep_hours_avg": "sleep_hours_avg",
    "workouts_per_week": "workouts_per_week",
    "meal_logs_per_week": "meal_logs_per_week",
}


async def compute_subgoal_compliance(session: AsyncSession, goal: Goal, subgoal: Subgoal, anchor: date_type) -> dict[str, Any]:
    target = float(subgoal.target_value)
    cutoff = anchor - timedelta(days=subgoal.window_days)
    # For count-style presets (workouts_per_week, meal_logs_per_week) the target
    # is "per week" but the compliance window may be longer/shorter. Scale the
    # target to match the window so a 14-day window compares against target*2.
    scaled_target = target
    current: float | None
    if subgoal.preset == "avg_kcal":
        res = await session.execute(
            select(func.avg(ManualLog.kcal_consumed)).where(
                ManualLog.user_id == goal.user_id,
                ManualLog.kcal_consumed.is_not(None),
                ManualLog.log_date >= cutoff, ManualLog.log_date <= anchor,
            )
        )
        v = res.scalar_one_or_none()
        current = float(v) if v is not None else None
    elif subgoal.preset == "protein_g_avg":
        res = await session.execute(
            select(func.avg(ManualLog.protein_g)).where(
                ManualLog.user_id == goal.user_id, ManualLog.protein_g.is_not(None),
                ManualLog.log_date >= cutoff, ManualLog.log_date <= anchor,
            )
        )
        v = res.scalar_one_or_none()
        current = float(v) if v is not None else None
    elif subgoal.preset == "sleep_hours_avg":
        res = await session.execute(
            select(func.avg(DailyMetrics.oura_sleep_duration_min)).where(
                DailyMetrics.user_id == goal.user_id,
                DailyMetrics.oura_sleep_duration_min.is_not(None),
                DailyMetrics.metric_date >= cutoff, DailyMetrics.metric_date <= anchor,
            )
        )
        v = res.scalar_one_or_none()
        current = float(v) / 60.0 if v is not None else None
    elif subgoal.preset == "workouts_per_week":
        window_days = subgoal.window_days
        res = await session.execute(
            select(func.count()).select_from(Workout).where(
                Workout.user_id == goal.user_id,
                Workout.workout_date >= anchor - timedelta(days=window_days),
                Workout.workout_date <= anchor,
            )
        )
        current = float(res.scalar() or 0)
        scaled_target = target * (window_days / 7.0)
    elif subgoal.preset == "meal_logs_per_week":
        window_days = subgoal.window_days
        res = await session.execute(
            select(func.count()).select_from(Meal).where(
                Meal.user_id == goal.user_id,
                Meal.meal_date >= anchor - timedelta(days=window_days),
                Meal.meal_date <= anchor,
            )
        )
        current = float(res.scalar() or 0)
        scaled_target = target * (window_days / 7.0)
    else:
        current = None

    if current is None:
        pct = 0.0
    elif subgoal.preset in {"workouts_per_week", "meal_logs_per_week"}:
        # scaled_target accounts for non-7-day windows; guard against zero.
        denom = scaled_target if scaled_target > 0 else 1.0
        pct = max(0.0, min(100.0, (current / denom) * 100))
    else:
        pct = max(0.0, 100.0 - abs(current - target) / target * 100)
    return {
        "preset": subgoal.preset, "target_value": target, "window_days": subgoal.window_days,
        "current_value": current, "compliance_pct": round(pct, 1),
    }


# ---- milestone-hit detection ------------------------------------------------

async def update_milestones(session: AsyncSession, goal: Goal, current_value: float | None, anchor: date_type) -> None:
    if current_value is None:
        return
    res = await session.execute(
        select(Milestone).where(Milestone.goal_id == goal.id, Milestone.hit_at.is_(None))
    )
    direction_down = _goal_direction_down(goal, current_value)
    for m in res.scalars().all():
        tv = float(m.target_value)
        hit = current_value <= tv if direction_down else current_value >= tv
        if hit:
            m.hit_at = datetime.now(timezone.utc)
            m.hit_value = current_value


# ---- action composition (deterministic) ------------------------------------

def compose_actions(
    goal: Goal, current_value: float | None, projection: dict[str, Any],
    regulation_rec_type: str, days_remaining: int,
    subgoal_compliances: list[dict[str, Any]],
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    p = projection.get("p_on_pace")

    if projection.get("method") == "insufficient_data":
        actions.append({
            "category": "data",
            "change": f"log {projection.get('min_required', 0) - projection.get('data_points_used', 0)} more measurements",
            "rationale": "projection requires more data points",
        })
    elif p is not None and p < 0.35:
        if goal.goal_type == "weight" and current_value is not None:
            gap = abs(float(goal.target_value) - (projection.get("projected_value_mean") or current_value))
            kcal_delta = int((gap * 3500) / max(days_remaining, 1))
            actions.append({
                "category": "nutrition",
                "change": f"reduce daily kcal by ~{kcal_delta}",
                "rationale": f"projected to miss target by {gap:.1f} lbs at current trajectory",
            })
        elif goal.goal_type == "strength":
            actions.append({
                "category": "training",
                "change": "add one set of the target lift per session",
                "rationale": "weekly gain rate below required pace",
            })
        elif goal.goal_type == "habit":
            actions.append({
                "category": "training",
                "change": f"add {max(1, int(float(goal.target_value) - (current_value or 0)))} workout this week",
                "rationale": "weekly workout count below target",
            })
        elif goal.goal_type == "recovery_hrv":
            actions.append({
                "category": "recovery",
                "change": "prioritize 7.5+ hours of sleep + protein at every meal",
                "rationale": "HRV trend below required improvement rate",
            })

    elif p is not None and p < 0.65:
        actions.append({
            "category": "compliance",
            "change": "tighten adherence to your subgoals",
            "rationale": f"trajectory uncertain (p_on_pace={p:.2f})",
        })

    for sg in subgoal_compliances:
        if sg["compliance_pct"] < 70:
            actions.append({
                "category": "compliance",
                "change": f"hit {sg['preset']} target ({sg['target_value']})",
                "rationale": f"current compliance: {sg['compliance_pct']}% (target 70%+)",
            })

    if regulation_rec_type == "deload":
        actions = [a for a in actions if a["category"] != "training"]
        actions.append({
            "category": "recovery",
            "change": "no additional training load this week",
            "rationale": "regulation engine flagged deload (HRV/sleep/strain)",
        })
    return actions[:5]


# ---- signals hash + LLM narration ------------------------------------------

def _signals_hash(
    goal_id: int,
    target_value: float,
    target_date: date_type,
    current_value: float | None,
    p_on_pace: float | None,
    subgoal_compliances: list[dict[str, Any]],
    regulation_rec_type: str,
) -> str:
    p_bucket = "none" if p_on_pace is None else (
        "off" if p_on_pace < 0.35 else ("uncertain" if p_on_pace < 0.65 else "on")
    )
    compl_rounded = sorted([(s["preset"], int(round(s["compliance_pct"] / 10) * 10)) for s in subgoal_compliances])
    payload = json.dumps({
        "goal_id": goal_id,
        "tv": round(target_value, 1),
        "td": target_date.isoformat(),
        "cv": None if current_value is None else round(current_value, 1),
        "p_bucket": p_bucket,
        "compl": compl_rounded,
        "reg": regulation_rec_type,
    }, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


async def _claude_narrate(goal: Goal, projection: dict[str, Any],
                          actions: list[dict[str, str]]) -> str:
    """Single haiku call; ~80 tokens. Returns a 1-2 sentence narration."""
    from anthropic import AsyncAnthropic
    settings = get_settings()
    if not settings.anthropic_api_key:
        return "Recommendation computed. (Narration unavailable: ANTHROPIC_API_KEY not set.)"
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    system = (
        "You are a recovery + training-readiness coach summarizing a daily goal recommendation. "
        "Output 1-2 short sentences combining the trajectory and the most-actionable suggestion. "
        "Be concrete, terse, no lists, no emoji."
    )
    user = (
        f"Goal: {goal.name} ({goal.goal_type}). "
        f"Trajectory: {projection}. "
        f"Actions: {actions}. "
        "Write the narration."
    )
    resp = await client.messages.create(
        model=settings.narration_model, max_tokens=200,
        system=system, messages=[{"role": "user", "content": user}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return " ".join(parts).strip() or "Recommendation computed."


# ---- main daily recompute --------------------------------------------------

async def daily_goal_recompute(
    session: AsyncSession,
    goal: Goal,
    anchor: date_type | None = None,
    commit: bool = True,
) -> None:
    anchor = anchor or date_type.today()
    current_value = await compute_current_value(session, goal, anchor)
    projection = await project_to_deadline(session, goal, anchor, current_value=current_value)
    await update_milestones(session, goal, current_value, anchor)

    signals = await compute_regulation_signals(session, user_id=goal.user_id, anchor=anchor)
    rec_type, _rationale, _payload = regulate(signals)

    res = await session.execute(select(Subgoal).where(Subgoal.goal_id == goal.id))
    subgoals = list(res.scalars().all())
    compliances = [await compute_subgoal_compliance(session, goal, sg, anchor) for sg in subgoals]

    days_remaining = max(0, (goal.target_date - anchor).days)
    actions = compose_actions(goal, current_value, projection, rec_type, days_remaining, compliances)

    sig = _signals_hash(
        goal.id,
        float(goal.target_value),
        goal.target_date,
        current_value,
        projection.get("p_on_pace"),
        compliances,
        rec_type,
    )
    cached = (await session.execute(
        select(GoalRecommendation).where(
            GoalRecommendation.goal_id == goal.id,
            GoalRecommendation.signals_hash == sig,
        ).order_by(GoalRecommendation.rec_date.desc()).limit(1)
    )).scalar_one_or_none()
    narration = cached.narration if cached else await _claude_narrate(goal, projection, actions)

    stmt = pg_insert(GoalRecommendation).values(
        goal_id=goal.id, rec_date=anchor, trajectory=projection, actions=actions,
        narration=narration, signals_hash=sig,
    ).on_conflict_do_update(
        index_elements=["goal_id", "rec_date"],
        set_={"trajectory": projection, "actions": actions, "narration": narration, "signals_hash": sig},
    )
    await session.execute(stmt)
    if commit:
        await session.commit()


async def run_for_all_active_goals(session: AsyncSession, user_id: str) -> None:
    """List active primary goals using the provided session, then recompute
    each in its own session so an error in one goal can't leave the shared
    session in a bad state for the next iteration.
    """
    from ..db import AsyncSessionLocal
    res = await session.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.status == "active", Goal.is_primary.is_(True))
    )
    goals = list(res.scalars().all())
    for g in goals:
        async with AsyncSessionLocal() as goal_session:
            try:
                await daily_goal_recompute(goal_session, g)
            except Exception:
                log.exception("goal_recompute_failed", goal_id=g.id)
                await goal_session.rollback()
