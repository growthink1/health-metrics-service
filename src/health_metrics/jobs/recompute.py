"""Recompute day-level manual_log nutrition fields from the meals table.

Single source of truth: rows in the `meals` table. The manual_log day row's
kcal_consumed / protein_g / fat_g / carbs_g fields are derived sums — kept
populated for backwards compatibility with dashboard surfaces (today strip,
log_status).
"""

from datetime import date as date_type

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ManualLog, Meal


async def recompute_day_aggregate(
    session: AsyncSession, user_id: str, day: date_type
) -> None:
    res = await session.execute(
        select(
            func.sum(Meal.kcal),
            func.sum(Meal.protein_g),
            func.sum(Meal.fat_g),
            func.sum(Meal.carbs_g),
        ).where(Meal.user_id == user_id, Meal.meal_date == day)
    )
    kcal, protein, fat, carbs = res.one()

    fields = {
        "kcal_consumed": int(kcal) if kcal is not None else None,
        "protein_g": int(protein) if protein is not None else None,
        "fat_g": int(fat) if fat is not None else None,
        "carbs_g": int(carbs) if carbs is not None else None,
    }
    stmt = (
        pg_insert(ManualLog)
        .values(user_id=user_id, log_date=day, **fields)
        .on_conflict_do_update(
            index_elements=["user_id", "log_date"],
            set_=fields,
        )
    )
    await session.execute(stmt)
    await session.commit()
