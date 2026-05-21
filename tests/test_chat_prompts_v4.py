"""build_system_prompt — active-goal block vs no-goal interview addendum."""

from datetime import date
from decimal import Decimal

import pytest

from health_metrics.chat_prompts import build_system_prompt
from health_metrics.models import Goal


@pytest.mark.asyncio
async def test_no_active_goal_appends_interview_addendum(db_session, test_user_id):
    prompt = await build_system_prompt(db_session, test_user_id)
    assert "no active primary goal" in prompt.lower()
    assert "Q1." in prompt
    assert "Q5." in prompt


@pytest.mark.asyncio
async def test_active_goal_appends_goal_block(db_session, test_user_id):
    g = Goal(
        user_id=test_user_id, goal_type="weight", name="Lose 15 lbs", metric="weight_lbs",
        target_value=Decimal("185"), start_date=date(2026, 5, 1), target_date=date(2026, 8, 1),
        is_primary=True, status="active",
    )
    db_session.add(g)
    await db_session.flush()

    prompt = await build_system_prompt(db_session, test_user_id)
    assert "Lose 15 lbs" in prompt
    assert "web_search" in prompt
    # The no-goal interview block should NOT appear
    assert "Q1." not in prompt
