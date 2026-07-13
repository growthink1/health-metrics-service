"""Tests for POST + PATCH revoke + GET /api/v1/regulation-overrides (spec §13)."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import RegulationCache, RegulationOverride


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


def _create_body(user_id: str, **kw) -> dict:
    body = {
        "user_id": user_id,
        "field": "kcal_target",
        "value": 2500,
        "justification": "doctor cleared the infection",
        "valid_from": "2026-05-27",
        "valid_until": "2026-06-01",
        "created_by": "hugo",
    }
    body.update(kw)
    return body


@pytest.mark.asyncio
async def test_override_post_401_without_token(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import regulation_overrides as ro_route

    monkeypatch.setattr(ro_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/regulation-overrides", json=_create_body(test_user_id))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_override_post_happy_path_and_invalidates_cache(db_session, monkeypatch, test_user_id):
    db_session.add(
        RegulationCache(
            user_id=test_user_id,
            as_of_date=date.today(),
            brief_json={"placeholder": True},
            latest_ingestion_at=datetime(2026, 5, 28, tzinfo=UTC),
            latest_write_at=datetime(2026, 5, 28, tzinfo=UTC),
        )
    )
    await db_session.flush()

    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import regulation_overrides as ro_route

    monkeypatch.setattr(ro_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/regulation-overrides",
            headers={"Authorization": "Bearer dash-tok"},
            json=_create_body(test_user_id),
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["field"] == "kcal_target"
    assert body["value"] == 2500
    assert body["created_by"] == "hugo"
    assert body["revoked_at"] is None
    uuid.UUID(body["id"])

    # cache invalidated for today
    r = await db_session.execute(
        select(RegulationCache).where(
            RegulationCache.user_id == test_user_id,
            RegulationCache.as_of_date == date.today(),
        )
    )
    assert r.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_override_post_rejects_bad_field(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import regulation_overrides as ro_route

    monkeypatch.setattr(ro_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/regulation-overrides",
            headers={"Authorization": "Bearer dash-tok"},
            json=_create_body(test_user_id, field="protein_target_g"),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_override_post_rejects_bad_created_by(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import regulation_overrides as ro_route

    monkeypatch.setattr(ro_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/regulation-overrides",
            headers={"Authorization": "Bearer dash-tok"},
            json=_create_body(test_user_id, created_by="dr_who"),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_override_revoke_happy_path(db_session, monkeypatch, test_user_id):
    ov = RegulationOverride(
        user_id=test_user_id,
        field="kcal_target",
        value=2500,
        justification="doctor cleared",
        valid_from=date(2026, 5, 27),
        valid_until=date(2026, 6, 1),
        created_by="hugo",
    )
    db_session.add(ov)
    await db_session.flush()
    override_id = ov.id

    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import regulation_overrides as ro_route

    monkeypatch.setattr(ro_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/regulation-overrides/{override_id}/revoke",
            headers={"Authorization": "Bearer dash-tok"},
            json={"reason": "recalibrated"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["revoked_at"] is not None
    assert body["revoked_reason"] == "recalibrated"


@pytest.mark.asyncio
async def test_override_revoke_401_without_token(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import regulation_overrides as ro_route

    monkeypatch.setattr(ro_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/regulation-overrides/{uuid.uuid4()}/revoke",
            json={"reason": "x"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_override_revoke_404_unknown_id(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import regulation_overrides as ro_route

    monkeypatch.setattr(ro_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/regulation-overrides/{uuid.uuid4()}/revoke",
            headers={"Authorization": "Bearer dash-tok"},
            json={"reason": "x"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_override_list_active_only_filters(db_session, monkeypatch, test_user_id):
    today = date.today()
    # active
    active = RegulationOverride(
        user_id=test_user_id,
        field="kcal_target",
        value=2500,
        justification="active",
        valid_from=today,
        valid_until=today,
        created_by="hugo",
    )
    # revoked
    revoked = RegulationOverride(
        user_id=test_user_id,
        field="kcal_target",
        value=2600,
        justification="revoked",
        valid_from=today,
        valid_until=today,
        created_by="hugo",
        revoked_at=datetime.now(UTC),
        revoked_reason="oops",
    )
    # expired (out of window)
    expired = RegulationOverride(
        user_id=test_user_id,
        field="kcal_target",
        value=2700,
        justification="expired",
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 1, 31),
        created_by="hugo",
    )
    db_session.add_all([active, revoked, expired])
    await db_session.flush()

    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import regulation_overrides as ro_route

    monkeypatch.setattr(ro_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # active_only=true → just the active one
        resp = await client.get(
            "/api/v1/regulation-overrides",
            headers={"Authorization": "Bearer dash-tok"},
            params={"user_id": test_user_id, "active_only": "true"},
        )
        assert resp.status_code == 200
        active_body = resp.json()
        assert len(active_body["overrides"]) == 1
        assert active_body["overrides"][0]["justification"] == "active"

        # no filter → all three
        resp_all = await client.get(
            "/api/v1/regulation-overrides",
            headers={"Authorization": "Bearer dash-tok"},
            params={"user_id": test_user_id},
        )
        assert resp_all.status_code == 200
        assert len(resp_all.json()["overrides"]) == 3
