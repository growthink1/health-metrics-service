import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text

from health_metrics.models import NarrationCache
from health_metrics.narration import generate_narration, signals_hash
from health_metrics.regulation import RegulationSignals


def _signals():
    return RegulationSignals(
        hrv_z_3d=-1.2, rhr_z_3d=0.4, sleep_3d_min=380,
        sleep_debt_min=180, strain_7d_total=85,
        subjective_3d_energy=6.0, days_with_complete_data=3,
    )


def test_signals_hash_is_deterministic():
    h1 = signals_hash(_signals())
    h2 = signals_hash(_signals())
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_signals_hash_changes_with_different_data():
    s1 = _signals()
    s2 = _signals()
    s2.hrv_z_3d = -1.3
    assert signals_hash(s1) != signals_hash(s2)


@pytest.mark.asyncio
async def test_generate_narration_calls_anthropic_and_caches(db_session):
    fake_response = AsyncMock()
    fake_response.content = [AsyncMock(text="HRV depressed 1.2σ — holding deficit pause.")]
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(return_value=fake_response)

    signals = _signals()
    rec = "maintenance"

    with patch("health_metrics.narration._build_client", return_value=fake_client):
        text_out = await generate_narration(
            db_session, user_id="hugo", metric_date=date(2026, 5, 13),
            recommendation=rec, signals=signals, commit=False,
        )

    assert text_out == "HRV depressed 1.2σ — holding deficit pause."
    fake_client.messages.create.assert_called_once()

    # Second call with same inputs should hit cache (no second Anthropic call)
    with patch("health_metrics.narration._build_client", return_value=fake_client):
        text_out2 = await generate_narration(
            db_session, user_id="hugo", metric_date=date(2026, 5, 13),
            recommendation=rec, signals=signals, commit=False,
        )

    assert text_out2 == text_out
    fake_client.messages.create.assert_called_once()  # still 1 call


@pytest.mark.asyncio
async def test_generate_narration_returns_none_when_api_key_missing(db_session, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Re-cache settings to pick up the delenv
    from health_metrics.config import get_settings
    get_settings.cache_clear()

    signals = _signals()
    result = await generate_narration(
        db_session, user_id="hugo", metric_date=date(2026, 5, 13),
        recommendation="maintenance", signals=signals, commit=False,
    )
    assert result is None
