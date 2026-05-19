"""Chat tool handlers — 3 writes + 2 reads + 4 v3 tools."""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from health_metrics.chat_tools import (
    TOOL_DEFINITIONS,
    log_subjective,
    log_weight,
    log_nutrition,
    get_recent_metrics,
    get_workouts,
    log_meal,
    log_manual_workout,
    log_workout_set,
    get_recent_meals,
    READ_TOOLS,
    WRITE_TOOLS,
)
from health_metrics.models import ManualLog, Meal, Workout, WorkoutSet


def test_tool_definitions_shape():
    assert len(TOOL_DEFINITIONS) == 9
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert names == {
        "log_subjective", "log_weight", "log_nutrition",
        "get_recent_metrics", "get_workouts",
        "log_meal", "log_manual_workout", "log_workout_set", "get_recent_meals",
    }
    for t in TOOL_DEFINITIONS:
        assert "name" in t and "description" in t and "input_schema" in t
        assert t["input_schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_log_weight_upserts(db_session, test_user_id):
    result = await log_weight(db_session, test_user_id, date(2026, 5, 17), 218.4)
    assert result["ok"] is True
    assert result["result"]["fields_updated"] == ["weight_lbs"]
    rows = (await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id)
    )).scalars().all()
    assert len(rows) == 1
    assert float(rows[0].weight_lbs) == 218.4


@pytest.mark.asyncio
async def test_log_subjective_partial_fields(db_session, test_user_id):
    result = await log_subjective(db_session, test_user_id, date(2026, 5, 17), energy=7, mood=8, hunger=None)
    assert result["ok"] is True
    assert set(result["result"]["fields_updated"]) == {"subjective_energy", "subjective_mood"}
    row = (await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id)
    )).scalar_one()
    assert row.subjective_energy == 7
    assert row.subjective_mood == 8
    assert row.subjective_hunger is None


@pytest.mark.asyncio
async def test_log_nutrition_upserts_macros(db_session, test_user_id):
    result = await log_nutrition(db_session, test_user_id, date(2026, 5, 17), kcal=2500, protein_g=180, fat_g=70, carbs_g=200)
    assert result["ok"] is True
    row = (await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id)
    )).scalar_one()
    assert row.kcal_consumed == 2500
    assert row.protein_g == 180


@pytest.mark.asyncio
async def test_log_weight_rejects_bad_date(db_session, test_user_id):
    result = await log_weight(db_session, test_user_id, "not-a-date", 218.0)
    assert result["ok"] is False
    assert "date" in result["error"].lower()


@pytest.mark.asyncio
async def test_get_recent_metrics_returns_json_list(db_session, test_user_id):
    # Seed 3 days
    await db_session.execute(text("""
        INSERT INTO daily_metrics (user_id, metric_date, oura_hrv_avg, whoop_day_strain, oura_status, whoop_status)
        VALUES (:u, '2026-05-15', 45, 10.0, 'ok', 'ok'),
               (:u, '2026-05-16', 47, 11.0, 'ok', 'ok'),
               (:u, '2026-05-17', 46, 13.0, 'ok', 'ok')
    """), {"u": test_user_id})
    await db_session.flush()

    result = await get_recent_metrics(db_session, test_user_id, days=7, anchor=date(2026, 5, 17))
    assert result["ok"] is True
    assert len(result["result"]["days"]) == 3
    last = result["result"]["days"][-1]
    assert last["date"] == "2026-05-17"
    assert last["hrv"] == 46
    assert last["strain"] == 13.0


@pytest.mark.asyncio
async def test_get_workouts_returns_list(db_session, test_user_id):
    await db_session.execute(text("""
        INSERT INTO workouts (user_id, workout_date, source, source_id, workout_type,
                              started_at, duration_min, strain)
        VALUES (:u, '2026-05-17', 'whoop', 'w-1', 'cycling',
                '2026-05-17T17:00:00+00:00'::timestamptz, 45, 12.5)
    """), {"u": test_user_id})
    await db_session.flush()
    result = await get_workouts(db_session, test_user_id, days=7, anchor=date(2026, 5, 17))
    assert result["ok"] is True
    assert len(result["result"]["workouts"]) == 1
    assert result["result"]["workouts"][0]["type"] == "cycling"


# ── v3 tool tests ────────────────────────────────────────────────────────────

def test_v3_tool_definitions_present():
    from health_metrics.chat_tools import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "log_meal" in names
    assert "log_manual_workout" in names
    assert "log_workout_set" in names
    assert "get_recent_meals" in names

    assert "get_recent_meals" in READ_TOOLS
    assert {"log_meal", "log_manual_workout", "log_workout_set"} <= WRITE_TOOLS


@pytest.mark.asyncio
async def test_log_meal_inserts_and_recomputes_aggregate(db_session, test_user_id):
    result = await log_meal(
        db_session, test_user_id,
        date="2026-05-19", meal_name="dinner",
        kcal=650, protein_g=40, fat_g=25, carbs_g=65,
    )
    assert result["ok"] is True
    rows = (await db_session.execute(
        select(Meal).where(Meal.user_id == test_user_id)
    )).scalars().all()
    assert len(rows) == 1
    ml = (await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id)
    )).scalar_one()
    assert ml.kcal_consumed == 650


@pytest.mark.asyncio
async def test_log_manual_workout_inserts_workouts_row(db_session, test_user_id):
    result = await log_manual_workout(
        db_session, test_user_id,
        date="2026-05-19", sport_name="running", duration_min=30, strain=5.0,
    )
    assert result["ok"] is True
    row = (await db_session.execute(
        select(Workout).where(Workout.user_id == test_user_id)
    )).scalar_one()
    assert row.source == "manual"
    assert row.workout_type == "running"


@pytest.mark.asyncio
async def test_log_workout_set_creates_placeholder_when_none_exists(db_session, test_user_id):
    result = await log_workout_set(
        db_session, test_user_id,
        workout_date="2026-05-19", exercise="back squat", reps=5, weight_lbs=315, rpe=8,
    )
    assert result["ok"] is True
    workouts = (await db_session.execute(
        select(Workout).where(Workout.user_id == test_user_id)
    )).scalars().all()
    assert len(workouts) == 1
    assert workouts[0].source == "manual"
    sets = (await db_session.execute(
        select(WorkoutSet).where(WorkoutSet.user_id == test_user_id)
    )).scalars().all()
    assert len(sets) == 1
    assert sets[0].exercise == "back squat"
    assert sets[0].set_number == 1


@pytest.mark.asyncio
async def test_log_workout_set_attaches_to_most_recent_existing(db_session, test_user_id):
    d = date(2026, 5, 19)
    db_session.add_all([
        Workout(
            user_id=test_user_id, workout_date=d, source="whoop", source_id="early",
            workout_type="strength",
            started_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
            duration_min=60,
        ),
        Workout(
            user_id=test_user_id, workout_date=d, source="whoop", source_id="late",
            workout_type="strength",
            started_at=datetime(2026, 5, 19, 18, 0, tzinfo=timezone.utc),
            duration_min=60,
        ),
    ])
    await db_session.flush()

    result = await log_workout_set(
        db_session, test_user_id,
        workout_date="2026-05-19", exercise="bench", reps=8,
    )
    assert result["ok"] is True
    sets = (await db_session.execute(
        select(WorkoutSet, Workout).join(Workout, WorkoutSet.workout_id == Workout.id)
        .where(WorkoutSet.user_id == test_user_id)
    )).all()
    assert len(sets) == 1
    s, w = sets[0]
    assert w.source_id == "late"


@pytest.mark.asyncio
async def test_log_workout_set_with_explicit_workout_id(db_session, test_user_id):
    d = date(2026, 5, 19)
    w = Workout(
        user_id=test_user_id, workout_date=d, source="manual", source_id=uuid4().hex,
        workout_type="strength",
        started_at=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        duration_min=0,
    )
    db_session.add(w)
    await db_session.flush()

    result = await log_workout_set(
        db_session, test_user_id,
        workout_id=w.id, exercise="deadlift", reps=3, weight_lbs=405,
    )
    assert result["ok"] is True
    rows = (await db_session.execute(
        select(WorkoutSet).where(WorkoutSet.workout_id == w.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].exercise == "deadlift"


@pytest.mark.asyncio
async def test_get_recent_meals_returns_last_n_days(db_session, test_user_id):
    today = date.today()
    db_session.add_all([
        Meal(user_id=test_user_id, meal_date=today, meal_name="today", kcal=500),
        Meal(user_id=test_user_id, meal_date=today, meal_name="today2", kcal=400),
    ])
    await db_session.flush()
    result = await get_recent_meals(db_session, test_user_id, days=7)
    assert result["ok"] is True
    assert len(result["result"]["meals"]) == 2
    names = {m["meal_name"] for m in result["result"]["meals"]}
    assert names == {"today", "today2"}


@pytest.mark.asyncio
async def test_log_meal_rejects_bad_date(db_session, test_user_id):
    result = await log_meal(db_session, test_user_id, date="bogus", kcal=500)
    assert result["ok"] is False
    assert "date" in result["error"].lower()
