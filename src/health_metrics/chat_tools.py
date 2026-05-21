"""Chat tool registry + handlers.

Tools exposed to Anthropic for the /api/chat endpoint, split into:
- Write tools (logging + goal mutations) — upsert/insert rows
- Read tools — fetch recent data for context

Write tools NEVER run server-side without a tool_confirmation: approved=True
arriving from the client. The chat route enforces that policy; this module
just exposes the handlers + definitions.
"""

from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .jobs.daily_goals import compute_current_value, daily_goal_recompute
from .jobs.recompute import recompute_day_aggregate
from .models import DailyMetrics, Goal, ManualLog, Meal, Milestone, Subgoal, Workout, WorkoutSet
from .routes.api import _read_metric  # reuse the Oura→Whoop fallback
from .routes.goals import get_goal_status_payload

log = structlog.get_logger()

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_recent_metrics",
        "description": "Read the user's daily health metrics (HRV, RHR, sleep, strain, recovery) for the last N days. Use this when the user asks about trends or compares their current state to recent history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 90, "description": "Number of days back from today"},
            },
            "required": ["days"],
        },
    },
    {
        "name": "get_workouts",
        "description": "Read the user's workout sessions for the last N days. Returns type, duration, strain, kcal per workout.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 90},
            },
            "required": ["days"],
        },
    },
    {
        "name": "log_subjective",
        "description": "Write the user's subjective ratings (energy, mood, hunger; each 1-10) for a given date into the manual_log table. The user MUST confirm before this runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO yyyy-mm-dd"},
                "energy": {"type": "integer", "minimum": 1, "maximum": 10},
                "mood": {"type": "integer", "minimum": 1, "maximum": 10},
                "hunger": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["date"],
        },
    },
    {
        "name": "log_weight",
        "description": "Write the user's weight (lbs) for a given date. The user MUST confirm before this runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO yyyy-mm-dd"},
                "weight_lbs": {"type": "number", "minimum": 50, "maximum": 500},
            },
            "required": ["date", "weight_lbs"],
        },
    },
    {
        "name": "log_nutrition",
        "description": "Write the user's nutrition (kcal + macros) for a given date. The user MUST confirm before this runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "kcal": {"type": "integer", "minimum": 0, "maximum": 10000},
                "protein_g": {"type": "integer", "minimum": 0, "maximum": 1000},
                "fat_g": {"type": "integer", "minimum": 0, "maximum": 1000},
                "carbs_g": {"type": "integer", "minimum": 0, "maximum": 2000},
            },
            "required": ["date"],
        },
    },
    {
        "name": "log_meal",
        "description": "Write a meal entry (kcal + macros + optional photo). The user MUST confirm before this runs. If photo_path is set, it must be a bucket key previously returned by the upload flow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO yyyy-mm-dd"},
                "time": {"type": "string", "description": "ISO HH:MM, optional"},
                "meal_name": {"type": "string"},
                "kcal": {"type": "integer", "minimum": 0, "maximum": 10000},
                "protein_g": {"type": "integer", "minimum": 0, "maximum": 1000},
                "fat_g": {"type": "integer", "minimum": 0, "maximum": 1000},
                "carbs_g": {"type": "integer", "minimum": 0, "maximum": 2000},
                "notes": {"type": "string"},
                "photo_path": {"type": "string"},
            },
            "required": ["date", "kcal"],
        },
    },
    {
        "name": "log_manual_workout",
        "description": "Log a workout the user did off-strap (Whoop did not capture it). The user MUST confirm before this runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "sport_name": {"type": "string"},
                "duration_min": {"type": "integer", "minimum": 1, "maximum": 600},
                "strain": {"type": "number", "minimum": 0, "maximum": 21},
                "kcal": {"type": "integer"},
                "notes": {"type": "string"},
            },
            "required": ["date", "sport_name", "duration_min"],
        },
    },
    {
        "name": "log_workout_set",
        "description": "Log one set for a strength workout. Pass workout_id when you know it; otherwise pass workout_date and the set attaches to the most recent workout on that date (or a placeholder manual workout is created if none exists).",
        "input_schema": {
            "type": "object",
            "properties": {
                "workout_id": {"type": "integer"},
                "workout_date": {"type": "string"},
                "exercise": {"type": "string"},
                "reps": {"type": "integer", "minimum": 1, "maximum": 100},
                "weight_lbs": {"type": "number", "minimum": 0, "maximum": 2000},
                "rpe": {"type": "number", "minimum": 1, "maximum": 10},
                "notes": {"type": "string"},
            },
            "required": ["exercise", "reps"],
        },
    },
    {
        "name": "get_recent_meals",
        "description": "Read the user's meals for the last N days. Read-only.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 90}},
            "required": ["days"],
        },
    },
    {
        "name": "set_primary_goal",
        "description": "Create the user's new primary goal. Automatically archives any existing active primary goal and auto-generates monthly milestones. User MUST confirm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_type":    {"type": "string", "enum": ["weight", "strength", "habit", "recovery_hrv"]},
                "name":         {"type": "string"},
                "metric":       {"type": "string"},
                "metric_params":{"type": "object"},
                "target_value": {"type": "number"},
                "target_date":  {"type": "string"},
            },
            "required": ["goal_type", "name", "metric", "target_value", "target_date"],
        },
    },
    {
        "name": "add_subgoal",
        "description": "Add a typed subgoal to the user's active primary goal. User MUST confirm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "preset":       {"type": "string", "enum": ["avg_kcal","workouts_per_week","sleep_hours_avg","protein_g_avg","meal_logs_per_week"]},
                "target_value": {"type": "number"},
                "window_days":  {"type": "integer", "minimum": 1, "maximum": 90},
            },
            "required": ["preset", "target_value"],
        },
    },
    {
        "name": "update_goal",
        "description": "Modify the primary goal's target value, target date, or lifecycle status. User MUST confirm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_value": {"type": "number"},
                "target_date":  {"type": "string"},
                "status":       {"type": "string", "enum": ["active","achieved","archived","missed"]},
            },
        },
    },
    {
        "name": "get_goal_status",
        "description": "Read-only snapshot of the active primary goal: current value, projection, p_on_pace, milestones, subgoal compliances, today's recommendation.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
]


def _parse_date(d: Any) -> date_type | None:
    if isinstance(d, date_type):
        return d
    if isinstance(d, str):
        try:
            return date_type.fromisoformat(d)
        except ValueError:
            return None
    return None


async def _upsert_manual_log(
    session: AsyncSession, user_id: str, log_date: date_type, fields: dict[str, Any]
) -> dict[str, Any]:
    """Insert-or-update a manual_log row, returning which fields were updated."""
    stmt = (
        pg_insert(ManualLog)
        .values(user_id=user_id, log_date=log_date, **fields)
        .on_conflict_do_update(
            index_elements=["user_id", "log_date"],
            set_=fields,
        )
    )
    await session.execute(stmt)
    await session.commit()
    return {"fields_updated": list(fields.keys()), "logged_date": log_date.isoformat()}


async def log_subjective(
    session: AsyncSession, user_id: str, date: Any,
    energy: Optional[int] = None, mood: Optional[int] = None, hunger: Optional[int] = None,
) -> dict[str, Any]:
    d = _parse_date(date)
    if d is None:
        return {"ok": False, "error": f"invalid date: {date!r}"}
    fields: dict[str, Any] = {}
    if energy is not None:
        fields["subjective_energy"] = energy
    if mood is not None:
        fields["subjective_mood"] = mood
    if hunger is not None:
        fields["subjective_hunger"] = hunger
    if not fields:
        return {"ok": False, "error": "no fields to update (provide at least one of energy/mood/hunger)"}
    return {"ok": True, "result": await _upsert_manual_log(session, user_id, d, fields)}


async def log_weight(
    session: AsyncSession, user_id: str, date: Any, weight_lbs: float,
) -> dict[str, Any]:
    d = _parse_date(date)
    if d is None:
        return {"ok": False, "error": f"invalid date: {date!r}"}
    return {"ok": True, "result": await _upsert_manual_log(session, user_id, d, {"weight_lbs": weight_lbs})}


async def log_nutrition(
    session: AsyncSession, user_id: str, date: Any,
    kcal: Optional[int] = None, protein_g: Optional[int] = None,
    fat_g: Optional[int] = None, carbs_g: Optional[int] = None,
) -> dict[str, Any]:
    d = _parse_date(date)
    if d is None:
        return {"ok": False, "error": f"invalid date: {date!r}"}
    fields: dict[str, Any] = {}
    if kcal is not None:
        fields["kcal_consumed"] = kcal
    if protein_g is not None:
        fields["protein_g"] = protein_g
    if fat_g is not None:
        fields["fat_g"] = fat_g
    if carbs_g is not None:
        fields["carbs_g"] = carbs_g
    if not fields:
        return {"ok": False, "error": "no fields to update"}
    return {"ok": True, "result": await _upsert_manual_log(session, user_id, d, fields)}


async def get_recent_metrics(
    session: AsyncSession, user_id: str, days: int, anchor: date_type | None = None,
) -> dict[str, Any]:
    anchor = anchor or date_type.today()
    start = anchor - timedelta(days=days - 1)
    res = await session.execute(
        select(DailyMetrics)
        .where(DailyMetrics.user_id == user_id)
        .where(DailyMetrics.metric_date >= start)
        .where(DailyMetrics.metric_date <= anchor)
        .order_by(DailyMetrics.metric_date.asc())
    )
    rows = list(res.scalars().all())
    days_out = [
        {
            "date": r.metric_date.isoformat(),
            "hrv": _read_metric(r, "hrv"),
            "rhr": _read_metric(r, "rhr"),
            "sleep_min": _read_metric(r, "sleep_min"),
            "strain": _read_metric(r, "strain"),
            "recovery": _read_metric(r, "recovery"),
        }
        for r in rows
    ]
    return {"ok": True, "result": {"days": days_out}}


async def get_workouts(
    session: AsyncSession, user_id: str, days: int, anchor: date_type | None = None,
) -> dict[str, Any]:
    anchor = anchor or date_type.today()
    start = anchor - timedelta(days=days - 1)
    res = await session.execute(
        select(Workout)
        .where(Workout.user_id == user_id)
        .where(Workout.workout_date >= start)
        .where(Workout.workout_date <= anchor)
        .order_by(Workout.workout_date.asc())
    )
    workouts = [
        {
            "date": w.workout_date.isoformat(),
            "type": w.workout_type,
            "duration_min": w.duration_min,
            "strain": float(w.strain) if w.strain is not None else None,
            "kcal": w.kcal,
        }
        for w in res.scalars().all()
    ]
    return {"ok": True, "result": {"workouts": workouts}}


async def log_meal(
    session: AsyncSession, user_id: str, date: Any,
    kcal: int, time: Optional[str] = None, meal_name: Optional[str] = None,
    protein_g: Optional[int] = None, fat_g: Optional[int] = None,
    carbs_g: Optional[int] = None, notes: Optional[str] = None,
    photo_path: Optional[str] = None,
) -> dict[str, Any]:
    d = _parse_date(date)
    if d is None:
        return {"ok": False, "error": f"invalid date: {date!r}"}
    from datetime import time as time_type
    t = None
    if time:
        try:
            t = time_type.fromisoformat(time)
        except ValueError:
            return {"ok": False, "error": f"invalid time: {time!r}"}

    meal = Meal(
        user_id=user_id, meal_date=d, meal_time=t, meal_name=meal_name,
        kcal=kcal, protein_g=protein_g, fat_g=fat_g, carbs_g=carbs_g,
        notes=notes, photo_path=photo_path, source="chat",
    )
    session.add(meal)
    await session.flush()
    meal_id = meal.id
    await recompute_day_aggregate(session, user_id, d)
    return {"ok": True, "result": {"meal_id": meal_id, "logged_date": d.isoformat(), "kcal": kcal}}


async def log_manual_workout(
    session: AsyncSession, user_id: str, date: Any,
    sport_name: str, duration_min: int,
    strain: Optional[float] = None, kcal: Optional[int] = None, notes: Optional[str] = None,
) -> dict[str, Any]:
    d = _parse_date(date)
    if d is None:
        return {"ok": False, "error": f"invalid date: {date!r}"}
    w = Workout(
        user_id=user_id, workout_date=d, source="manual", source_id=uuid4().hex,
        workout_type=sport_name,
        started_at=datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
        duration_min=duration_min, strain=strain, kcal=kcal,
        raw={"notes": notes} if notes else None,
    )
    session.add(w)
    await session.flush()
    workout_id = w.id
    await session.commit()
    return {"ok": True, "result": {"workout_id": workout_id, "logged_date": d.isoformat(), "type": sport_name}}


async def log_workout_set(
    session: AsyncSession, user_id: str,
    exercise: str, reps: int,
    workout_id: Optional[int] = None, workout_date: Optional[str] = None,
    weight_lbs: Optional[float] = None, rpe: Optional[float] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    target_wid = workout_id
    if target_wid is None:
        d = _parse_date(workout_date) if workout_date else date_type.today()
        if d is None:
            return {"ok": False, "error": f"invalid workout_date: {workout_date!r}"}
        latest = (await session.execute(
            select(Workout).where(Workout.user_id == user_id, Workout.workout_date == d)
            .order_by(Workout.started_at.desc()).limit(1)
        )).scalar_one_or_none()
        if latest is None:
            placeholder = Workout(
                user_id=user_id, workout_date=d, source="manual", source_id=uuid4().hex,
                workout_type="strength",
                started_at=datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc),
                duration_min=0,
            )
            session.add(placeholder)
            await session.flush()
            target_wid = placeholder.id
        else:
            target_wid = latest.id

    max_n = (await session.execute(
        select(func.max(WorkoutSet.set_number))
        .where(WorkoutSet.workout_id == target_wid)
        .where(WorkoutSet.exercise == exercise)
    )).scalar()
    next_n = (max_n or 0) + 1

    s = WorkoutSet(
        user_id=user_id, workout_id=target_wid, set_number=next_n,
        exercise=exercise, reps=reps, weight_lbs=weight_lbs, rpe=rpe, notes=notes,
    )
    session.add(s)
    await session.flush()
    set_id = s.id
    await session.commit()
    return {"ok": True, "result": {
        "set_id": set_id, "workout_id": target_wid, "set_number": next_n,
        "exercise": exercise, "reps": reps,
    }}


async def get_recent_meals(
    session: AsyncSession, user_id: str, days: int,
    anchor: date_type | None = None,
) -> dict[str, Any]:
    anchor = anchor or date_type.today()
    start = anchor - timedelta(days=days - 1)
    res = await session.execute(
        select(Meal)
        .where(Meal.user_id == user_id)
        .where(Meal.meal_date >= start)
        .where(Meal.meal_date <= anchor)
        .order_by(Meal.meal_date.asc(), Meal.created_at.asc())
    )
    meals = [
        {
            "date": m.meal_date.isoformat(),
            "time": m.meal_time.isoformat() if m.meal_time else None,
            "meal_name": m.meal_name,
            "kcal": m.kcal,
            "protein_g": m.protein_g,
            "fat_g": m.fat_g,
            "carbs_g": m.carbs_g,
            "has_photo": bool(m.photo_path),
        }
        for m in res.scalars().all()
    ]
    return {"ok": True, "result": {"meals": meals}}


def _generate_milestones(start: date_type, target: date_type, start_value, target_value) -> list[dict]:
    span = (target - start).days
    if span <= 35:
        points = [target]
    elif span <= 70:
        points = [start + timedelta(days=span // 2), target]
    else:
        # monthly checkpoints
        points = []
        cur = start + timedelta(days=30)
        while cur < target:
            points.append(cur)
            cur += timedelta(days=30)
        points.append(target)
    out = []
    for d in points:
        frac = (d - start).days / max(span, 1)
        v = float(start_value) + frac * (float(target_value) - float(start_value))
        out.append({"target_value": round(v, 1), "target_date": d})
    return out


async def _initial_goal_recompute(session: AsyncSession, goal: Goal) -> None:
    """Compute the first day's recommendation immediately after goal creation."""
    await daily_goal_recompute(session, goal)


async def set_primary_goal(
    session: AsyncSession, user_id: str,
    goal_type: str, name: str, metric: str,
    target_value: float, target_date: str,
    metric_params: dict | None = None,
) -> dict[str, Any]:
    td = _parse_date(target_date)
    if td is None:
        return {"ok": False, "error": f"invalid target_date: {target_date!r}"}
    today = date_type.today()
    if td <= today:
        return {"ok": False, "error": "target_date must be in the future"}

    # Archive any existing primary active goal
    res = await session.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.status == "active", Goal.is_primary.is_(True))
    )
    for prior in res.scalars().all():
        prior.status = "archived"

    # Compute start_value lazily
    placeholder = Goal(
        user_id=user_id, goal_type=goal_type, name=name, metric=metric,
        metric_params=metric_params, target_value=Decimal(str(target_value)),
        start_date=today, target_date=td, is_primary=True, status="active",
    )
    session.add(placeholder)
    await session.flush()
    sv = await compute_current_value(session, placeholder, today)
    if sv is not None:
        placeholder.start_value = Decimal(str(sv))

    # Milestones
    sv_for_ms = float(placeholder.start_value) if placeholder.start_value is not None else float(target_value)
    for m in _generate_milestones(today, td, sv_for_ms, target_value):
        session.add(Milestone(goal_id=placeholder.id,
                              target_value=Decimal(str(m["target_value"])),
                              target_date=m["target_date"]))
    await session.flush()
    goal_id = placeholder.id
    await session.commit()

    # Initial recompute — best-effort. Goal is already persisted; if narration
    # generation fails (Anthropic down, etc.), surface a warning but keep the
    # successful goal-creation result.
    warning: str | None = None
    try:
        await _initial_goal_recompute(session, placeholder)
    except Exception as exc:
        log.warning("initial_goal_recompute_failed", goal_id=goal_id, error=str(exc))
        warning = "initial_recompute_failed"

    result: dict[str, Any] = {
        "goal_id": goal_id,
        "name": name,
        "start_value": float(placeholder.start_value) if placeholder.start_value else None,
    }
    if warning:
        result["warning"] = warning
    return {"ok": True, "result": result}


async def add_subgoal(
    session: AsyncSession, user_id: str, preset: str, target_value: float,
    window_days: int = 7,
) -> dict[str, Any]:
    g = (await session.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.status == "active", Goal.is_primary.is_(True))
    )).scalar_one_or_none()
    if g is None:
        return {"ok": False, "error": "no active primary goal — set one first with set_primary_goal"}
    sg = Subgoal(goal_id=g.id, preset=preset, target_value=Decimal(str(target_value)), window_days=window_days)
    session.add(sg)
    await session.flush()
    await session.commit()
    return {"ok": True, "result": {"subgoal_id": sg.id, "preset": preset, "target_value": float(target_value)}}


async def update_goal(
    session: AsyncSession, user_id: str,
    target_value: float | None = None,
    target_date: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    if target_value is None and target_date is None and status is None:
        return {"ok": False, "error": "no fields to update — pass target_value, target_date, or status"}
    g = (await session.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.status == "active", Goal.is_primary.is_(True))
    )).scalar_one_or_none()
    if g is None:
        return {"ok": False, "error": "no active primary goal"}
    if target_date is not None:
        td = _parse_date(target_date)
        if td is None or td <= date_type.today():
            return {"ok": False, "error": "target_date must be in the future"}
        g.target_date = td
    if target_value is not None:
        g.target_value = Decimal(str(target_value))
    if status is not None:
        g.status = status
    g.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await session.commit()
    return {"ok": True, "result": {"goal_id": g.id, "target_date": g.target_date.isoformat(),
                                   "target_value": float(g.target_value), "status": g.status}}


async def get_goal_status(session: AsyncSession, user_id: str) -> dict[str, Any]:
    payload = await get_goal_status_payload(session, user_id)
    return {"ok": True, "result": payload}


# Dispatch table — chat route looks up tool name → handler.
TOOL_HANDLERS = {
    "get_recent_metrics": get_recent_metrics,
    "get_workouts": get_workouts,
    "get_recent_meals": get_recent_meals,
    "log_subjective": log_subjective,
    "log_weight": log_weight,
    "log_nutrition": log_nutrition,
    "log_meal": log_meal,
    "log_manual_workout": log_manual_workout,
    "log_workout_set": log_workout_set,
}
TOOL_HANDLERS.update({
    "set_primary_goal": set_primary_goal,
    "add_subgoal": add_subgoal,
    "update_goal": update_goal,
    "get_goal_status": get_goal_status,
})

READ_TOOLS = {"get_recent_metrics", "get_workouts", "get_recent_meals"}
READ_TOOLS.add("get_goal_status")
WRITE_TOOLS = {"log_subjective", "log_weight", "log_nutrition", "log_meal", "log_manual_workout", "log_workout_set"}
WRITE_TOOLS.update({"set_primary_goal", "add_subgoal", "update_goal"})
