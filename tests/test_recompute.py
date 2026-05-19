"""Day-aggregate recompute — sum meals into manual_log."""

from datetime import date

import pytest
from sqlalchemy import select

from health_metrics.jobs.recompute import recompute_day_aggregate
from health_metrics.models import ManualLog, Meal


@pytest.mark.asyncio
async def test_recompute_sums_meals_into_manual_log(db_session, test_user_id):
    target = date(2026, 5, 19)
    db_session.add_all([
        Meal(user_id=test_user_id, meal_date=target, kcal=400, protein_g=30, fat_g=15, carbs_g=40, source="chat"),
        Meal(user_id=test_user_id, meal_date=target, kcal=650, protein_g=40, fat_g=25, carbs_g=65, source="chat"),
        Meal(user_id=test_user_id, meal_date=target, kcal=200, protein_g=10, fat_g=8, carbs_g=20, source="chat"),
    ])
    await db_session.flush()

    await recompute_day_aggregate(db_session, test_user_id, target)

    res = await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id, ManualLog.log_date == target)
    )
    ml = res.scalar_one()
    assert ml.kcal_consumed == 1250
    assert ml.protein_g == 80
    assert ml.fat_g == 48
    assert ml.carbs_g == 125


@pytest.mark.asyncio
async def test_recompute_with_no_meals_yields_nulls(db_session, test_user_id):
    target = date(2026, 5, 19)
    await recompute_day_aggregate(db_session, test_user_id, target)

    res = await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id, ManualLog.log_date == target)
    )
    ml = res.scalar_one()
    assert ml.kcal_consumed is None
    assert ml.protein_g is None


@pytest.mark.asyncio
async def test_recompute_preserves_unrelated_manual_log_fields(db_session, test_user_id):
    """If a manual_log row already has weight_lbs / subjective scores, recompute
    must not clobber them — only the nutrition fields are touched."""
    target = date(2026, 5, 19)
    db_session.add(ManualLog(
        user_id=test_user_id, log_date=target, weight_lbs=218.4,
        subjective_energy=7, subjective_mood=8, subjective_hunger=5,
    ))
    db_session.add(Meal(user_id=test_user_id, meal_date=target, kcal=500, protein_g=30, fat_g=20, carbs_g=50))
    await db_session.flush()

    await recompute_day_aggregate(db_session, test_user_id, target)

    res = await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id, ManualLog.log_date == target)
    )
    ml = res.scalar_one()
    assert ml.kcal_consumed == 500
    assert float(ml.weight_lbs) == 218.4
    assert ml.subjective_energy == 7
