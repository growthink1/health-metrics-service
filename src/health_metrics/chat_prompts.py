"""System prompt builder for /api/chat.

Seeds Claude with the user's current recommendation, recent metrics, and the
list of tools available. The prompt is built fresh per chat request from
live DB state — chat is not multi-turn-stateful on the backend (the client
sends the full message history each request).
"""

from datetime import date as date_type
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .chat_tools import TOOL_DEFINITIONS
from .models import DailyMetrics, Goal, GoalRecommendation
# The 7-state engine (via the legacy adapter) is the single source of truth
# for rec_type / rationale / action — Invariant #1.
# compute_regulation_signals() is still consumed here for the body block that
# surfaces hrv_z_3d / rhr_z_3d / sleep_debt / strain_total / subjective averages
# to Claude. That dataclass remains in regulation/legacy.py until the prompt
# body is migrated to the new SessionBrief shape (separate cleanup PR).
from .regulation import compute_regulation_signals
from .regulation.legacy_adapter import compute_legacy_recommendation
from .routes.api import _read_metric

_INTERVIEW_ADDENDUM = """
The user has no active primary goal. If they ask for help setting one, conduct
a brief structured interview — one question per turn, multiple-choice when sensible:

  Q1. What kind of goal? Options: weight / strength PR / habit consistency / recovery / HRV improvement.
  Q2. Based on Q1, ask for the specific metric (target lbs, exercise + reps, preset + target, target HRV).
  Q3. Target date (default: 12 weeks from today; offer to adjust).
  Q4. Suggest 2-3 typed subgoals appropriate for the goal type. Ask the user to confirm or modify.
  Q5. Summarize the proposed goal + subgoals. Then call set_primary_goal. Subgoals attach via
      separate add_subgoal calls AFTER the primary goal is confirmed.

Push back (not block) on unreasonable timelines: weight loss > 2 lb/wk, target date < 14 days
(non-habit), strength gain > 5% / wk, HRV target > +10 ms over current. State the literature
norm + risk, ask the user to confirm explicitly.

Ask ONE question per turn. Don't call set_primary_goal until Q5 summary is approved.
"""


async def build_system_prompt(
    session: AsyncSession,
    user_id: str,
    anchor: date_type | None = None,
    image_hints: list[str] | None = None,
) -> str:
    """Compose the system prompt for the /api/chat Anthropic call."""
    anchor = anchor or date_type.today()

    # 1. Today's recommendation — via the 7-state engine (Invariant #1).
    rec_type, rationale, action = await compute_legacy_recommendation(session, user_id, anchor)
    # Signals dataclass still feeds the body block below until the prompt is
    # migrated to the new SessionBrief shape.
    signals = await compute_regulation_signals(session, user_id=user_id, anchor=anchor)
    suggested_kcal = action.get("kcal", "N/A")
    suggested_training_mod = action.get("training", "N/A")

    # 2. Last 30 days of compact metrics
    start = anchor - timedelta(days=29)
    res = await session.execute(
        select(DailyMetrics)
        .where(DailyMetrics.user_id == user_id)
        .where(DailyMetrics.metric_date >= start)
        .where(DailyMetrics.metric_date <= anchor)
        .order_by(DailyMetrics.metric_date.asc())
    )
    rows = list(res.scalars().all())
    compact: list[dict[str, Any]] = []
    for r in rows:
        compact.append({
            "date": r.metric_date.isoformat(),
            "hrv": _read_metric(r, "hrv"),
            "rhr": _read_metric(r, "rhr"),
            "sleep_min": _read_metric(r, "sleep_min"),
            "strain": _read_metric(r, "strain"),
            "recovery": _read_metric(r, "recovery"),
        })

    tool_names = ", ".join(t["name"] for t in TOOL_DEFINITIONS)

    base = f"""You are a recovery + training-readiness coach for the user of health-metrics, a personal Whoop + Oura analytics dashboard. The user is a single individual (Hugo); this is a private single-user tool.

Today is {anchor.isoformat()}.

Today's auto-regulation recommendation: {rec_type.upper()}
- Suggested kcal: {suggested_kcal}
- Training mod: {suggested_training_mod}
- Rationale: {'; '.join(rationale)}

Today's signals (relative to user baseline):
- HRV today: {compact[-1].get('hrv') if compact else 'no data'} ms
- HRV z (3-day avg): {signals.hrv_z_3d:.2f}σ
- RHR z (3-day avg): {signals.rhr_z_3d:.2f}σ
- Sleep debt: {signals.sleep_debt_min} min
- 7-day strain total: {signals.strain_7d_total:.1f}
- Subjective 3-day energy avg: {signals.subjective_3d_energy or 'unlogged'}

Recent 30 days (compact JSON, oldest first):
{compact}

Available tools: {tool_names}.

Behavior rules:
- Answer questions about the user's metrics, trends, and recovery state. Be concise and grounded in the numbers above; cite specific dates and values when useful.
- When the user asks to log something ('log my weight 218', 'energy was 7 today'), call the appropriate write tool (log_subjective / log_weight / log_nutrition). The user will see your tool call and confirm before it runs — you don't need to ask them to confirm in chat text; just call the tool with the right args.
- Default dates to today ({anchor.isoformat()}) unless the user specifies a different date.
- If the user asks for advice, give it briefly. You are a coach, not a doctor; if something looks medically concerning, suggest they talk to their physician.
- Use the read tools (get_recent_metrics, get_workouts) only if the data above doesn't cover what they're asking. The 30-day window is usually enough.
"""

    image_block = ""
    if image_hints:
        keys = "\n".join(f"  - {k}" for k in image_hints)
        image_block = (
            f"\n\nThe user attached image(s) in this message. They have been saved to bucket key(s):\n{keys}\n"
            "If you decide to call `log_meal` based on image content, pass the bucket key as `photo_path` "
            "so the meal row references the saved photo."
        )

    prompt = base + image_block

    # Goal addendum
    res = await session.execute(
        select(Goal)
        .where(Goal.user_id == user_id, Goal.status == "active", Goal.is_primary.is_(True))
        .order_by(Goal.created_at.desc())
        .limit(1)
    )
    goal = res.scalar_one_or_none()
    if goal is None:
        return prompt + "\n\n" + _INTERVIEW_ADDENDUM.strip()

    # Active-goal block
    rec_res = await session.execute(
        select(GoalRecommendation)
        .where(GoalRecommendation.goal_id == goal.id)
        .order_by(GoalRecommendation.rec_date.desc())
        .limit(1)
    )
    rec = rec_res.scalar_one_or_none()
    narration = rec.narration if rec else "(no recommendation yet)"
    p = rec.trajectory.get("p_on_pace") if rec else None
    p_str = "no projection yet" if p is None else f"{p:.2f}"
    block = (
        f"\n\nActive primary goal: \"{goal.name}\" — {goal.goal_type} from "
        f"{goal.start_value if goal.start_value is not None else 'unknown'} → "
        f"{goal.target_value} by {goal.target_date.isoformat()}.\n"
        f"Today's recommendation: {narration}\n"
        f"P_on_pace: {p_str}.\n"
        "Available goal tools: set_primary_goal, add_subgoal, update_goal, get_goal_status.\n"
        "You also have web_search — use it when the user asks for best-practice context "
        "(e.g. 'how fast can someone lose 15 lbs?'). Cite sources."
    )
    return prompt + block
