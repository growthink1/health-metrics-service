"""Daily goal recompute — happy path, cache reuse, milestone hit."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from health_metrics.jobs import daily_goals
from health_metrics.models import Goal, GoalRecommendation, Milestone, ManualLog


async def _make_goal(session, uid):
    g = Goal(
        user_id=uid, goal_type="weight", name="Lose 15 lbs", metric="weight_lbs",
        start_value=Decimal("200"), target_value=Decimal("185"),
        start_date=date(2026, 4, 1), target_date=date(2026, 7, 1),
        is_primary=True, status="active",
    )
    session.add(g)
    await session.flush()
    return g


@pytest.mark.asyncio
async def test_recompute_writes_recommendation_row(db_session, monkeypatch, test_user_id):
    # Seed 30 days of weight + a goal
    for i in range(30):
        db_session.add(ManualLog(
            user_id=test_user_id, log_date=date(2026, 4, 1) + timedelta(days=i),
            weight_lbs=Decimal(str(200 - 0.1 * i)),
        ))
    g = await _make_goal(db_session, test_user_id)
    await db_session.flush()

    monkeypatch.setattr(daily_goals, "_claude_narrate",
                        AsyncMock(return_value="On track — keep adherence steady."))

    await daily_goals.daily_goal_recompute(db_session, g, anchor=date(2026, 5, 1))

    row = (await db_session.execute(
        select(GoalRecommendation).where(GoalRecommendation.goal_id == g.id)
    )).scalar_one()
    assert row.narration == "On track — keep adherence steady."
    assert row.trajectory["method"] == "bayesian_normal_normal"
    assert isinstance(row.actions, list)
    assert len(row.actions) <= 5


@pytest.mark.asyncio
async def test_recompute_reuses_cache_on_unchanged_signals(db_session, monkeypatch, test_user_id):
    for i in range(30):
        db_session.add(ManualLog(
            user_id=test_user_id, log_date=date(2026, 4, 1) + timedelta(days=i),
            weight_lbs=Decimal(str(200 - 0.1 * i)),
        ))
    g = await _make_goal(db_session, test_user_id)
    await db_session.flush()

    narrate = AsyncMock(return_value="Initial narration.")
    monkeypatch.setattr(daily_goals, "_claude_narrate", narrate)

    await daily_goals.daily_goal_recompute(db_session, g, anchor=date(2026, 5, 1))
    await daily_goals.daily_goal_recompute(db_session, g, anchor=date(2026, 5, 1))
    # Second call same-day with same signals → cache hit → narrate called once
    assert narrate.call_count == 1


@pytest.mark.asyncio
async def test_recompute_invalidates_cache_when_target_changes(db_session, monkeypatch, test_user_id):
    """C2 followup: target_value/target_date are part of the signals hash, so
    mutating either on the in-memory goal between calls must miss the cache
    and trigger a fresh narration."""
    for i in range(30):
        db_session.add(ManualLog(
            user_id=test_user_id, log_date=date(2026, 4, 1) + timedelta(days=i),
            weight_lbs=Decimal(str(200 - 0.1 * i)),
        ))
    g = await _make_goal(db_session, test_user_id)
    await db_session.flush()

    narrate = AsyncMock(return_value="First narration.")
    monkeypatch.setattr(daily_goals, "_claude_narrate", narrate)

    await daily_goals.daily_goal_recompute(db_session, g, anchor=date(2026, 5, 1))
    # Move the deadline a month later — same anchor, same observations, but a
    # different target_date should change the signals hash and force a recompute.
    g.target_date = date(2026, 8, 1)
    await daily_goals.daily_goal_recompute(db_session, g, anchor=date(2026, 5, 1))
    assert narrate.call_count == 2


@pytest.mark.asyncio
async def test_recompute_marks_milestone_hit(db_session, monkeypatch, test_user_id):
    for i in range(30):
        db_session.add(ManualLog(
            user_id=test_user_id, log_date=date(2026, 4, 1) + timedelta(days=i),
            weight_lbs=Decimal(str(200 - 0.5 * i)),  # aggressive loss
        ))
    g = await _make_goal(db_session, test_user_id)
    db_session.add(Milestone(goal_id=g.id, target_value=Decimal("195"), target_date=date(2026, 5, 15)))
    await db_session.flush()

    monkeypatch.setattr(daily_goals, "_claude_narrate", AsyncMock(return_value="Ahead of pace."))
    # Current value at anchor is ≈ 200 - 0.5*29 = 185.5, well past 195
    await daily_goals.daily_goal_recompute(db_session, g, anchor=date(2026, 5, 1))

    m = (await db_session.execute(
        select(Milestone).where(Milestone.goal_id == g.id)
    )).scalar_one()
    assert m.hit_at is not None
    assert float(m.hit_value) < 195
