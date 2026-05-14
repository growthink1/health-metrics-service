from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import ManualLog


@pytest.mark.asyncio
async def test_manual_log_upserts_subjective_then_weight(db_session, monkeypatch):
    from contextlib import asynccontextmanager
    from health_metrics.routes import api as api_route

    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(api_route, "_session_factory", lambda: _ctx())

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First POST: subjective markers only
        resp1 = await client.post("/api/manual-log", json={
            "user_id": "hugo",
            "date": "2026-05-13",
            "subjective_energy": 6,
            "subjective_mood": 7,
            "subjective_hunger": 5,
        })
        assert resp1.status_code == 200
        body = resp1.json()
        assert set(body["fields_updated"]) == {"subjective_energy", "subjective_mood", "subjective_hunger"}
        assert body["completeness"]["subjective"] is True
        assert body["completeness"]["weight"] is False

        # Second POST: add weight
        resp2 = await client.post("/api/manual-log", json={
            "user_id": "hugo",
            "date": "2026-05-13",
            "weight_lbs": 218.4,
        })
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["fields_updated"] == ["weight_lbs"]
        assert body2["completeness"]["subjective"] is True  # still True (merged)
        assert body2["completeness"]["weight"] is True

    # Verify single row in DB
    res = await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == "hugo", ManualLog.log_date == date(2026, 5, 13))
    )
    rows = res.scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.subjective_energy == 6
    assert float(row.weight_lbs) == 218.4
