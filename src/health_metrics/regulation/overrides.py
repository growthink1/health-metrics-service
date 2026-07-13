"""Fetch + apply active regulation overrides on top of the engine's RegulationCall.

Kept OUT of the pure engine (compute_regulation stays I/O-free, Invariant #2).
compute_session_brief calls apply_overrides() after compute_regulation()."""

from datetime import date as date_type
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import RegulationOverride
from .schemas import AppliedOverride, RegulationCall, RegulationState, TrainingModifier


async def fetch_active_overrides(session: AsyncSession, user_id: str, as_of: date_type) -> list[RegulationOverride]:
    """Active = not revoked AND valid_from <= as_of <= valid_until. Oldest first
    so the most-recent (last applied) wins per field."""
    r = await session.execute(
        select(RegulationOverride)
        .where(
            RegulationOverride.user_id == user_id,
            RegulationOverride.revoked_at.is_(None),
            RegulationOverride.valid_from <= as_of,
            RegulationOverride.valid_until >= as_of,
        )
        .order_by(RegulationOverride.created_at.asc())
    )
    return list(r.scalars().all())


def apply_overrides(call: RegulationCall, overrides: list[RegulationOverride]) -> RegulationCall:
    """Return a NEW RegulationCall with active overrides applied. Most-recent
    (later in the list) wins per field. Records each applied change in
    applied_overrides. Pure — no I/O."""
    kcal = call.kcal_target
    state = call.state
    tmod = call.training_modifier
    ov_list = list(call.overrides_today)
    applied: list[AppliedOverride] = []

    for o in overrides:
        v: Any = o.value
        if o.field == "kcal_target":
            applied.append(
                AppliedOverride(
                    field="kcal_target",
                    from_value=str(kcal),
                    to_value=str(v),
                    justification=o.justification,
                )
            )
            kcal = int(v)
        elif o.field == "state":
            applied.append(
                AppliedOverride(
                    field="state",
                    from_value=str(state),
                    to_value=str(v),
                    justification=o.justification,
                )
            )
            state = RegulationState(v)
        elif o.field == "training_modifier":
            applied.append(
                AppliedOverride(
                    field="training_modifier",
                    from_value=str(tmod),
                    to_value=str(v),
                    justification=o.justification,
                )
            )
            tmod = TrainingModifier(v)
        elif o.field == "add_override":
            if v not in ov_list:
                ov_list.append(str(v))
            applied.append(
                AppliedOverride(
                    field="add_override",
                    from_value="-",
                    to_value=str(v),
                    justification=o.justification,
                )
            )
        elif o.field == "remove_override":
            if v in ov_list:
                ov_list.remove(v)
            applied.append(
                AppliedOverride(
                    field="remove_override",
                    from_value=str(v),
                    to_value="-",
                    justification=o.justification,
                )
            )

    return call.model_copy(
        update={
            "kcal_target": kcal,
            "state": state,
            "training_modifier": tmod,
            "overrides_today": ov_list,
            "applied_overrides": applied,
        }
    )
