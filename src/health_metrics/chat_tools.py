"""Chat tool registry + handlers.

Five tools exposed to Anthropic for the /api/chat endpoint:
- 3 write tools (log_subjective, log_weight, log_nutrition) — upsert manual_log rows
- 2 read tools (get_recent_metrics, get_workouts) — fetch recent data for context

Write tools NEVER run server-side without a tool_confirmation: approved=True
arriving from the client. The chat route enforces that policy; this module
just exposes the handlers + definitions.
"""

from datetime import date as date_type, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DailyMetrics, ManualLog, Workout
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


# Dispatch table — chat route looks up tool name → handler.
TOOL_HANDLERS = {
    "get_recent_metrics": get_recent_metrics,
    "get_workouts": get_workouts,
    "log_subjective": log_subjective,
    "log_weight": log_weight,
    "log_nutrition": log_nutrition,
}

READ_TOOLS = {"get_recent_metrics", "get_workouts"}
WRITE_TOOLS = {"log_subjective", "log_weight", "log_nutrition"}
