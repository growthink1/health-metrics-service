"""End-to-end integration: POST /manual-entry → cache invalidates → next /session-brief
recomputes → confidence flips from 'medium' to 'high' because subjective_48h is no
longer missing.

Spec acceptance criterion: 'POST /manual-entry for user_id=hugo, entry_date=2026-05-28,
energy_1_10=7, hunger_1_10=8, soreness_1_10=3 — verify next get_session_brief returns
confidence=high instead of medium (no longer missing subjective_48h).'"""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import DailyMetrics, ManualLog, RegulationCache


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_manual_entry_round_trip_flips_confidence(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")

    # Seed 20 days of daily_metrics so history_days_count >= 14 AND today has Oura+Whoop present.
    # Without this seeding, confidence would stay 'low' (3 missing) — we want exactly subjective_48h missing.
    today = date.today()
    for i in range(20):
        dm_date = today - timedelta(days=i)
        db_session.add(
            DailyMetrics(
                user_id=test_user_id,
                metric_date=dm_date,
                oura_sleep_duration_min=420,
                oura_hrv_avg=45,
                oura_rhr=58,
                whoop_recovery_score=75,
                whoop_day_strain=8.0,
                unified_hrv_z=0.2,
                unified_rhr_z=-0.1,
                ingestion_complete=True,
                ingested_at=datetime.now(UTC),
            )
        )
    await db_session.flush()

    # Patch session factories for BOTH routes we hit (sequence: session-brief → manual-entry → session-brief)
    from health_metrics.routes import manual_entry as me_route
    from health_metrics.routes import session_brief as sb_route

    monkeypatch.setattr(sb_route, "_session_factory", lambda: _ctx(db_session))
    monkeypatch.setattr(me_route, "_session_factory", lambda: _ctx(db_session))

    from health_metrics.main import app

    headers = {"Authorization": "Bearer dash-tok"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1) Cold cache read — confidence should be 'medium' (only subjective_48h missing)
        r = await client.get(f"/api/v1/session-brief?user_id={test_user_id}", headers=headers)
        assert r.status_code == 200
        first = r.json()
        assert first["confidence"] == "medium"
        missing_fields = {m["field"] for m in first["missing_inputs"]}
        assert "subjective_48h" in missing_fields

        # 2) POST manual-entry using BOTH semantic and native names (proves alias works in the round-trip)
        post = await client.post(
            "/api/v1/manual-entry",
            headers=headers,
            json={
                "user_id": test_user_id,
                "entry_date": today.isoformat(),  # alias
                "energy_1_10": 7,  # alias
                "hunger_1_10": 8,  # alias
                "soreness_1_10": 3,  # native (no alias)
            },
        )
        assert post.status_code == 201

        # 3) Verify DB has DB-aligned column names populated correctly
        rr = await db_session.execute(
            select(ManualLog).where(ManualLog.user_id == test_user_id, ManualLog.log_date == today)
        )
        row = rr.scalar_one()
        assert row.subjective_energy == 7
        assert row.subjective_hunger == 8
        assert row.soreness_1_10 == 3

        # 4) Confirm cache was invalidated by the write
        rr = await db_session.execute(
            select(RegulationCache).where(
                RegulationCache.user_id == test_user_id,
                RegulationCache.as_of_date == today,
            )
        )
        assert rr.scalar_one_or_none() is None

        # 5) Second read — confidence flips to 'high' (no more missing inputs)
        r = await client.get(f"/api/v1/session-brief?user_id={test_user_id}", headers=headers)
        assert r.status_code == 200
        second = r.json()
        assert second["confidence"] == "high", (
            f"expected high after subjective write, got {second['confidence']} "
            f"missing={[m['field'] for m in second['missing_inputs']]}"
        )
        missing_after = {m["field"] for m in second["missing_inputs"]}
        assert "subjective_48h" not in missing_after
