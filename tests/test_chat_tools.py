"""Chat tool handlers — 3 writes + 2 reads."""

from datetime import date

import pytest
from sqlalchemy import select, text

from health_metrics.chat_tools import (
    TOOL_DEFINITIONS,
    log_subjective,
    log_weight,
    log_nutrition,
    get_recent_metrics,
    get_workouts,
)
from health_metrics.models import ManualLog


def test_tool_definitions_shape():
    # 5 tools, each with name + description + input_schema
    assert len(TOOL_DEFINITIONS) == 5
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert names == {"log_subjective", "log_weight", "log_nutrition", "get_recent_metrics", "get_workouts"}
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
