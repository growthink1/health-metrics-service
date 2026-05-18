"""System prompt builder — bakes today's recommendation + recent data into the seed."""

from datetime import date

import pytest
from sqlalchemy import text

from health_metrics.chat_prompts import build_system_prompt


@pytest.mark.asyncio
async def test_system_prompt_contains_recommendation_and_recent_data(db_session, test_user_id):
    # Seed 7 days of metrics with a deload-y profile
    await db_session.execute(text("""
        INSERT INTO daily_metrics (user_id, metric_date,
            oura_hrv_avg, oura_rhr, oura_sleep_duration_min,
            unified_hrv_z, unified_rhr_z, whoop_sleep_debt_min,
            whoop_day_strain, oura_status, whoop_status, ingestion_complete)
        VALUES
            (:u, '2026-05-11', 45, 60, 400, -1.0, 0.5, 200, 12.0, 'ok', 'ok', TRUE),
            (:u, '2026-05-12', 47, 58, 410, -0.8, 0.3, 180, 11.0, 'ok', 'ok', TRUE),
            (:u, '2026-05-13', 46, 59, 380, -1.2, 0.7, 220, 13.0, 'ok', 'ok', TRUE)
    """), {"u": test_user_id})
    await db_session.execute(text(
        "INSERT INTO manual_log (user_id, log_date, subjective_energy) VALUES (:u, '2026-05-13', 5)"
    ), {"u": test_user_id})
    await db_session.flush()

    prompt = await build_system_prompt(db_session, user_id=test_user_id, anchor=date(2026, 5, 13))

    # Contains the key sections we want Claude to see
    assert "health-metrics" in prompt.lower() or "recovery" in prompt.lower()
    assert "DELOAD" in prompt.upper() or "MAINTENANCE" in prompt.upper() or "DEFICIT" in prompt.upper()
    assert "2026-05-13" in prompt  # most recent day
    # The tools section
    assert "log_subjective" in prompt or "tools" in prompt.lower()


@pytest.mark.asyncio
async def test_system_prompt_handles_empty_db(db_session, test_user_id):
    # Empty DB for this user — should still produce a valid prompt, just without recent data
    prompt = await build_system_prompt(db_session, user_id=test_user_id, anchor=date(2026, 5, 13))
    assert isinstance(prompt, str)
    assert len(prompt) > 100  # has some content
