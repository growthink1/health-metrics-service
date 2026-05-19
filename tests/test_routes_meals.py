"""POST/GET/DELETE /api/meals + photo proxy + multipart upload."""

from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import Meal, ManualLog


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_post_meal_inserts_row_and_recomputes_aggregate(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import meals as meals_route
    monkeypatch.setattr(meals_route, "_session_factory", lambda: _ctx(db_session))

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/meals", json={
            "user_id": test_user_id,
            "date": "2026-05-19",
            "meal_name": "chicken stir fry",
            "kcal": 650, "protein_g": 40, "fat_g": 25, "carbs_g": 65,
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["meal"]["meal_name"] == "chicken stir fry"
    assert body["meal"]["kcal"] == 650
    assert body["aggregate"]["kcal_consumed"] == 650

    meals = (await db_session.execute(
        select(Meal).where(Meal.user_id == test_user_id)
    )).scalars().all()
    assert len(meals) == 1
    ml = (await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id)
    )).scalar_one()
    assert ml.kcal_consumed == 650


@pytest.mark.asyncio
async def test_get_meals_returns_day_ordered(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import meals as meals_route
    monkeypatch.setattr(meals_route, "_session_factory", lambda: _ctx(db_session))

    db_session.add_all([
        Meal(user_id=test_user_id, meal_date=date(2026, 5, 19), meal_name="lunch", kcal=500),
        Meal(user_id=test_user_id, meal_date=date(2026, 5, 19), meal_name="dinner", kcal=700),
        Meal(user_id=test_user_id, meal_date=date(2026, 5, 18), meal_name="other day", kcal=300),
    ])
    await db_session.flush()

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/meals?user_id={test_user_id}&date=2026-05-19")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["meals"]) == 2
    names = [m["meal_name"] for m in body["meals"]]
    assert names == ["lunch", "dinner"]


@pytest.mark.asyncio
async def test_delete_meal_removes_row_and_recomputes(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import meals as meals_route
    monkeypatch.setattr(meals_route, "_session_factory", lambda: _ctx(db_session))

    db_session.add_all([
        Meal(user_id=test_user_id, meal_date=date(2026, 5, 19), kcal=500),
        Meal(user_id=test_user_id, meal_date=date(2026, 5, 19), kcal=700),
    ])
    await db_session.flush()
    res = await db_session.execute(
        select(Meal).where(Meal.user_id == test_user_id).order_by(Meal.id.asc())
    )
    m1, _m2 = res.scalars().all()

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/meals/{m1.id}?user_id={test_user_id}")
    assert resp.status_code == 200

    remaining = (await db_session.execute(
        select(Meal).where(Meal.user_id == test_user_id)
    )).scalars().all()
    assert len(remaining) == 1
    ml = (await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id)
    )).scalar_one()
    assert ml.kcal_consumed == 700


@pytest.mark.asyncio
async def test_meals_upload_multipart_writes_to_bucket(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import meals as meals_route
    monkeypatch.setattr(meals_route, "_session_factory", lambda: _ctx(db_session))

    fake_storage = MagicMock()
    fake_storage.upload_with_sha.return_value = "meals/deadbeef.jpg"
    monkeypatch.setattr(meals_route, "get_storage", lambda: fake_storage)

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/meals/upload",
            files={"photo": ("dinner.jpg", b"\xff\xd8\xff fakejpegbytes", "image/jpeg")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["photo_path"] == "meals/deadbeef.jpg"
    fake_storage.upload_with_sha.assert_called_once()
    args, kwargs = fake_storage.upload_with_sha.call_args
    # Photo bytes were passed in (either positionally or by keyword)
    actual_data = args[0] if args else kwargs.get("data")
    assert actual_data == b"\xff\xd8\xff fakejpegbytes"
    # Prefix is "meals"
    actual_prefix = kwargs.get("prefix")
    if actual_prefix is None and len(args) >= 2:
        actual_prefix = args[1]
    assert actual_prefix == "meals"


@pytest.mark.asyncio
async def test_meals_photo_proxy_streams_bytes(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import meals as meals_route
    monkeypatch.setattr(meals_route, "_session_factory", lambda: _ctx(db_session))

    fake_storage = MagicMock()
    fake_storage.stream.return_value = iter([b"chunk1", b"chunk2"])
    monkeypatch.setattr(meals_route, "get_storage", lambda: fake_storage)

    db_session.add(Meal(
        user_id=test_user_id, meal_date=date(2026, 5, 19),
        kcal=500, photo_path="meals/abc.jpg",
    ))
    await db_session.flush()
    meal_id = (await db_session.execute(
        select(Meal.id).where(Meal.user_id == test_user_id)
    )).scalar_one()

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/meals/{meal_id}/photo?user_id={test_user_id}")
    assert resp.status_code == 200
    assert resp.content == b"chunk1chunk2"
    fake_storage.stream.assert_called_once_with("meals/abc.jpg")


@pytest.mark.asyncio
async def test_post_meal_rejects_bad_date(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import meals as meals_route
    monkeypatch.setattr(meals_route, "_session_factory", lambda: _ctx(db_session))

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/meals", json={
            "user_id": test_user_id, "date": "not-a-date", "kcal": 500,
        })
    assert resp.status_code in (400, 422)
