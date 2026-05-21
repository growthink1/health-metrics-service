"""Goal status + history REST endpoints. Same payload shape as the get_goal_status chat tool."""

from contextlib import asynccontextmanager
from datetime import date as date_type, timedelta
from typing import Any, AsyncIterator

import structlog
from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import AsyncSessionLocal
from ..jobs.daily_goals import compute_subgoal_compliance
from ..models import Goal, GoalRecommendation, Milestone, Subgoal

log = structlog.get_logger()
router = APIRouter(prefix="/api")


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx():
        async with AsyncSessionLocal() as session:
            yield session
    return _ctx()


def _goal_to_json(g: Goal) -> dict[str, Any]:
    return {
        "id": g.id, "name": g.name, "goal_type": g.goal_type, "metric": g.metric,
        "metric_params": g.metric_params,
        "start_value": float(g.start_value) if g.start_value is not None else None,
        "target_value": float(g.target_value),
        "start_date": g.start_date.isoformat(), "target_date": g.target_date.isoformat(),
        "status": g.status,
    }


async def get_goal_status_payload(session: AsyncSession, user_id: str) -> dict[str, Any]:
    """Build the GoalStatus payload — shared by the route + chat tool."""
    res = await session.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.status == "active", Goal.is_primary.is_(True))
        .order_by(Goal.created_at.desc()).limit(1)
    )
    goal = res.scalar_one_or_none()
    if goal is None:
        return {"goal": None, "trajectory": None, "milestones": [], "subgoals": [], "recommendation": None}

    res = await session.execute(
        select(Milestone).where(Milestone.goal_id == goal.id).order_by(Milestone.target_date.asc())
    )
    milestones = [
        {
            "target_value": float(m.target_value), "target_date": m.target_date.isoformat(),
            "hit_at": m.hit_at.isoformat() if m.hit_at else None,
            "hit_value": float(m.hit_value) if m.hit_value is not None else None,
        }
        for m in res.scalars().all()
    ]

    res = await session.execute(select(Subgoal).where(Subgoal.goal_id == goal.id))
    subgoals_rows = list(res.scalars().all())
    today = date_type.today()
    subgoals = [await compute_subgoal_compliance(session, goal, sg, today) for sg in subgoals_rows]

    res = await session.execute(
        select(GoalRecommendation).where(GoalRecommendation.goal_id == goal.id)
        .order_by(GoalRecommendation.rec_date.desc()).limit(1)
    )
    rec = res.scalar_one_or_none()
    recommendation = None
    trajectory = None
    if rec is not None:
        trajectory = rec.trajectory
        recommendation = {
            "rec_date": rec.rec_date.isoformat(),
            "narration": rec.narration,
            "actions": list(rec.actions),
        }

    return {
        "goal": _goal_to_json(goal),
        "trajectory": trajectory,
        "milestones": milestones,
        "subgoals": subgoals,
        "recommendation": recommendation,
    }


@router.get("/goals/status")
async def goal_status(user_id: str | None = Query(default=None)) -> dict[str, Any]:
    settings = get_settings()
    uid = user_id or settings.user_id
    async with _session_factory() as session:
        return await get_goal_status_payload(session, uid)


@router.get("/goals/history")
async def goal_history(user_id: str | None = Query(default=None), days: int = Query(default=7, ge=1, le=90)) -> dict[str, Any]:
    settings = get_settings()
    uid = user_id or settings.user_id
    async with _session_factory() as session:
        goal_row = (await session.execute(
            select(Goal).where(Goal.user_id == uid, Goal.status == "active", Goal.is_primary.is_(True))
            .order_by(Goal.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if goal_row is None:
            return {"rows": []}
        res = await session.execute(
            select(GoalRecommendation).where(GoalRecommendation.goal_id == goal_row.id)
            .order_by(GoalRecommendation.rec_date.desc()).limit(days)
        )
        rows = [
            {"rec_date": r.rec_date.isoformat(), "narration": r.narration, "actions": list(r.actions)}
            for r in res.scalars().all()
        ]
        return {"rows": rows}
