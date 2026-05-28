"""Bridge from the new 7-state engine back to the legacy 4-state tuple.

Allows existing callers (chat_prompts, daily_goals, routes/api dashboard
endpoint) to continue rendering the legacy shape while the engine itself is
the single source of truth (spec Invariant #1).

Once the dashboard HeroBlock + chat_prompts UX are updated to render the new
state names directly, this adapter becomes dead code and can be removed.
"""

from datetime import date as date_type
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .brief import compute_session_brief
from .schemas import RegulationCall, RegulationState

# Legacy state → (legacy rec_type, training-mod string, kcal target)
_STATE_TO_LEGACY: dict[RegulationState, tuple[str, str, int]] = {
    RegulationState.DEFICIT: (
        "deficit",
        "Full program, progression OK",
        2300,
    ),
    RegulationState.DEFICIT_CONSERVATIVE: (
        "deficit_conservative",
        "Full program, monitor closely",
        2500,
    ),
    RegulationState.MAINTENANCE_SLEEP_DEFICIT: (
        "deload",
        "Volume -30%, Z2 only, extra rest day",
        2800,
    ),
    RegulationState.MAINTENANCE_ILLNESS: (
        "deload",
        "REST — no training",
        2800,
    ),
    RegulationState.MAINTENANCE_PRE_PROCEDURE: (
        "maintenance",
        "Pre-procedure: Volume -20%, no Z4+, RPE cap 7",
        2800,
    ),
    RegulationState.MAINTENANCE_HRV_DEPRESSION: (
        "maintenance",
        "Volume -30%, swap HIIT for Z2",
        2800,
    ),
}


def map_to_legacy(call: RegulationCall) -> tuple[str, list[str], dict[str, Any]]:
    """Pure mapping — testable without a DB."""
    rec_type, training, default_kcal = _STATE_TO_LEGACY[call.state]
    # Prefer the engine's kcal_target so adapter stays in sync with future engine tuning.
    kcal = call.kcal_target if call.kcal_target else default_kcal
    return rec_type, list(call.rationale), {"kcal": kcal, "training": training}


async def compute_legacy_recommendation(
    session: AsyncSession, user_id: str, anchor: date_type
) -> tuple[str, list[str], dict[str, Any]]:
    """Build a full SessionBrief (cache-aware not required here — callers are
    on-request) and return the legacy 3-tuple."""
    brief = await compute_session_brief(session, user_id, anchor)
    return map_to_legacy(brief.regulation_call)
