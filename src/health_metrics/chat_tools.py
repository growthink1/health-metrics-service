"""Chat tool registry + handlers.

Nine tools exposed to Anthropic for the /api/chat endpoint:
- 6 write tools (log_subjective, log_weight, log_nutrition, log_meal,
  log_manual_workout, log_workout_set) — upsert/insert rows
- 3 read tools (get_recent_metrics, get_workouts, get_recent_meals)
  — fetch recent data for context

Write tools NEVER run server-side without a tool_confirmation: approved=True
arriving from the client. The chat route enforces that policy; this module
just exposes the handlers + definitions.
"""

from datetime import date as date_type, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .jobs.recompute import recompute_day_aggregate
from .models import DailyMetrics, ManualLog, Meal, Workout, WorkoutSet
from .routes.api import _read_metric  # reuse the Oura→Whoop fallback


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

READ_TOOLS = {"get_recent_metrics", "get_workouts", "get_recent_meals"}
WRITE_TOOLS = {"log_subjective", "log_weight", "log_nutrition", "log_meal", "log_manual_workout", "log_workout_set"}
