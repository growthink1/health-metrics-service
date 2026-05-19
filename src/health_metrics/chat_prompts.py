"""System prompt builder for /api/chat.

Seeds Claude with the user's current recommendation, recent metrics, and the
list of tools available. The prompt is built fresh per chat request from
live DB state — chat is not multi-turn-stateful on the backend (the client
sends the full message history each request).
"""

from datetime import date as date_type, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .chat_tools import TOOL_DEFINITIONS
from .regulation import compute_regulation_signals, regulate
from .routes.api import _read_metric
from .models import DailyMetrics
from sqlalchemy import select


async def build_system_prompt(
    session: AsyncSession,
    user_id: str,
    anchor: date_type | None = None,
    image_hints: list[str] | None = None,
) -> str:
    """Compose the system prompt for the /api/chat Anthropic call."""
    anchor = anchor or date_type.today()

    # 1. Today's recommendation
    signals = await compute_regulation_signals(session, user_id=user_id, anchor=anchor)
    # regulate() returns (RecType, rationale_list, action_payload)
    rec_type, rationale, action = regulate(signals)
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

    return base + image_block
