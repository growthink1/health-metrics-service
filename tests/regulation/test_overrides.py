"""Tests for regulation override fetch + application (spec §13).

apply_overrides is pure (no I/O); fetch_active_overrides is exercised here with a
mocked AsyncSession so this file stays free of real async-engine creation. (On
macOS the asyncpg C extension segfaults under the coverage tracer's greenlet
stack — the repo's own CI coverage command over tests/regulation/ reproduces it
locally at test_brief.py. Keeping this file engine-free lets the overrides.py
coverage gate run cleanly.) The real-DB filter semantics of fetch_active_overrides
are validated by the /api/v1/regulation-overrides GET active_only route test and
the brief end-to-end test in tests/regulation/test_brief.py.

Together the tests below hit 100% branch coverage on overrides.py.
"""

from datetime import date as date_type
from unittest.mock import AsyncMock, MagicMock

import pytest

from health_metrics.models import RegulationOverride
from health_metrics.regulation.overrides import apply_overrides, fetch_active_overrides
from health_metrics.regulation.schemas import RegulationCall, RegulationState, TrainingModifier


def _base_call(**overrides) -> RegulationCall:
    defaults = dict(
        state=RegulationState.DEFICIT,
        training_modifier=TrainingModifier.FULL_PROGRESSION,
        kcal_target=2800,
        overrides_today=[],
        rationale=["All signals green"],
        signals_considered=[],
        confidence="high",
    )
    defaults.update(overrides)
    return RegulationCall(**defaults)


def _ov(field: str, value, justification: str = "because", **kw) -> RegulationOverride:
    return RegulationOverride(
        user_id="hugo",
        field=field,
        value=value,
        justification=justification,
        valid_from=date_type(2026, 5, 27),
        valid_until=date_type(2026, 6, 1),
        created_by="hugo",
        **kw,
    )


def test_empty_overrides_returns_unchanged_call():
    call = _base_call()
    out = apply_overrides(call, [])
    assert out.kcal_target == 2800
    assert out.state == RegulationState.DEFICIT
    assert out.training_modifier == TrainingModifier.FULL_PROGRESSION
    assert out.overrides_today == []
    assert out.applied_overrides == []
    # original untouched (model_copy returns a new object)
    assert call.applied_overrides == []


def test_kcal_target_override_changes_kcal_and_records():
    call = _base_call(kcal_target=2800)
    out = apply_overrides(call, [_ov("kcal_target", 2500, justification="doctor cleared")])
    assert out.kcal_target == 2500
    assert len(out.applied_overrides) == 1
    ao = out.applied_overrides[0]
    assert ao.field == "kcal_target"
    assert ao.from_value == "2800"
    assert ao.to_value == "2500"
    assert ao.justification == "doctor cleared"


def test_state_override_changes_state():
    call = _base_call(state=RegulationState.DEFICIT)
    out = apply_overrides(call, [_ov("state", "MAINTENANCE_ILLNESS")])
    assert out.state == RegulationState.MAINTENANCE_ILLNESS
    assert out.applied_overrides[0].field == "state"
    assert out.applied_overrides[0].from_value == "DEFICIT"
    assert out.applied_overrides[0].to_value == "MAINTENANCE_ILLNESS"


def test_training_modifier_override_changes_modifier():
    call = _base_call(training_modifier=TrainingModifier.FULL_PROGRESSION)
    out = apply_overrides(call, [_ov("training_modifier", "REST")])
    assert out.training_modifier == TrainingModifier.REST
    assert out.applied_overrides[0].field == "training_modifier"
    assert out.applied_overrides[0].from_value == "FULL_PROGRESSION"
    assert out.applied_overrides[0].to_value == "REST"


def test_add_override_appends_when_absent():
    call = _base_call(overrides_today=[])
    out = apply_overrides(call, [_ov("add_override", "no_z4_plus")])
    assert "no_z4_plus" in out.overrides_today
    assert out.overrides_today.count("no_z4_plus") == 1
    ao = out.applied_overrides[0]
    assert ao.field == "add_override"
    assert ao.from_value == "-"
    assert ao.to_value == "no_z4_plus"


def test_add_override_no_duplicate_when_present():
    call = _base_call(overrides_today=["no_z4_plus"])
    out = apply_overrides(call, [_ov("add_override", "no_z4_plus")])
    # not duplicated
    assert out.overrides_today.count("no_z4_plus") == 1
    # still recorded as applied
    assert out.applied_overrides[0].field == "add_override"


def test_remove_override_removes_when_present():
    call = _base_call(overrides_today=["no_z4_plus", "watch_jaw_load"])
    out = apply_overrides(call, [_ov("remove_override", "no_z4_plus")])
    assert "no_z4_plus" not in out.overrides_today
    assert "watch_jaw_load" in out.overrides_today
    ao = out.applied_overrides[0]
    assert ao.field == "remove_override"
    assert ao.from_value == "no_z4_plus"
    assert ao.to_value == "-"


def test_remove_override_noop_when_absent():
    call = _base_call(overrides_today=["watch_jaw_load"])
    out = apply_overrides(call, [_ov("remove_override", "no_z4_plus")])
    assert out.overrides_today == ["watch_jaw_load"]
    # still recorded
    assert out.applied_overrides[0].field == "remove_override"


def test_unknown_field_is_ignored():
    """Defensive: a field the applier doesn't recognize is a silent no-op (all
    branches of the elif chain fall through). The DB CHECK + Literal prevent
    unknown fields being stored, but apply_overrides stays robust regardless."""
    call = _base_call(kcal_target=2800, overrides_today=["no_z4_plus"])
    out = apply_overrides(call, [_ov("mystery_field", 999)])
    assert out.kcal_target == 2800
    assert out.overrides_today == ["no_z4_plus"]
    assert out.applied_overrides == []


def test_multiple_fields_in_one_pass():
    """remove_override is NOT the last item -> exercises the loop-back edge after
    the remove branch; also confirms mixed fields all apply in one pass."""
    call = _base_call(kcal_target=2800, overrides_today=["no_z4_plus"])
    out = apply_overrides(
        call,
        [
            _ov("remove_override", "no_z4_plus"),
            _ov("kcal_target", 2400),
            _ov("add_override", "watch_jaw_load"),
        ],
    )
    assert "no_z4_plus" not in out.overrides_today
    assert "watch_jaw_load" in out.overrides_today
    assert out.kcal_target == 2400
    assert len(out.applied_overrides) == 3


def test_most_recent_wins_same_field():
    call = _base_call(kcal_target=2800)
    # list is oldest-first; later entries win
    out = apply_overrides(
        call,
        [
            _ov("kcal_target", 2500, justification="first"),
            _ov("kcal_target", 2400, justification="second"),
        ],
    )
    assert out.kcal_target == 2400
    assert len(out.applied_overrides) == 2
    # both recorded, in order
    assert out.applied_overrides[0].to_value == "2500"
    assert out.applied_overrides[1].to_value == "2400"


@pytest.mark.asyncio
async def test_fetch_active_overrides_returns_scalars_list():
    """Covers fetch_active_overrides' query build + return path against a mocked
    session (no real engine). Real WHERE-clause semantics are covered by the
    route GET active_only test + the brief end-to-end test."""
    sentinel = [_ov("kcal_target", 2500), _ov("state", "MAINTENANCE_ILLNESS")]
    result = MagicMock()
    result.scalars.return_value.all.return_value = sentinel
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    rows = await fetch_active_overrides(session, "hugo", date_type(2026, 5, 28))

    assert rows == sentinel
    assert session.execute.await_count == 1
    # a statement object was passed to execute (the built SELECT)
    assert session.execute.await_args.args[0] is not None


@pytest.mark.asyncio
async def test_fetch_active_overrides_empty():
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    rows = await fetch_active_overrides(session, "hugo", date_type(2026, 5, 28))
    assert rows == []
