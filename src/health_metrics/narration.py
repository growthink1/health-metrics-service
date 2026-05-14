"""Claude-generated narration for the dashboard Today strip.

One-sentence explanation of the auto-regulation recommendation, generated
by Claude Haiku. Content-addressed cache — keyed on
(user_id, metric_date, sha256(canonical_signals_json)). New narration only
when signals actually change.
"""

import hashlib
import json
from dataclasses import asdict
from datetime import date

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import NarrationCache
from .regulation import RegulationSignals

log = structlog.get_logger()


PROMPT_TEMPLATE = (
    "You are Hugo's health-and-fitness analytical assistant. Given today's "
    "auto-regulation output and the underlying signals, write ONE concise "
    "sentence (max 25 words) explaining the recommendation in physiological "
    "terms Hugo will recognize. Be specific about the numbers driving the "
    "call. Don't hedge.\n\n"
    "Today's recommendation: {recommendation}\n"
    "Triggering signals (JSON): {signals_json}\n\n"
    "Conservative bias is built into the rules — your job is to explain, "
    "not second-guess. Return ONLY the sentence, no prefix or quotes."
)


def signals_hash(signals: RegulationSignals) -> str:
    """SHA256 of the canonical-JSON serialization of signals."""
    payload = json.dumps(asdict(signals), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_client():
    """Return an Anthropic AsyncClient or None if no API key."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    from anthropic import AsyncAnthropic
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


async def generate_narration(
    session: AsyncSession,
    user_id: str,
    metric_date: date,
    recommendation: str,
    signals: RegulationSignals,
    commit: bool = True,
) -> str | None:
    """Return a narration sentence for the given inputs. None if API key absent.

    Pulls from `narration_cache` if a row already exists for the same
    (user_id, metric_date, signals_hash). Otherwise calls Anthropic, caches
    the result, and returns it.
    """
    h = signals_hash(signals)

    # Cache lookup
    res = await session.execute(
        select(NarrationCache).where(
            NarrationCache.user_id == user_id,
            NarrationCache.metric_date == metric_date,
            NarrationCache.signals_hash == h,
        )
    )
    cached = res.scalar_one_or_none()
    if cached:
        return cached.narration_text

    client = _build_client()
    if client is None:
        log.warning("narration_no_api_key", user_id=user_id, date=metric_date.isoformat())
        return None

    settings = get_settings()
    prompt = PROMPT_TEMPLATE.format(
        recommendation=recommendation,
        signals_json=json.dumps(asdict(signals), sort_keys=True),
    )

    try:
        response = await client.messages.create(
            model=settings.narration_model,
            max_tokens=settings.narration_max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        narration_text = response.content[0].text.strip()
    except Exception as e:
        log.error(
            "narration_api_failed",
            user_id=user_id,
            date=metric_date.isoformat(),
            error=str(e),
        )
        return None

    # Upsert cache
    stmt = pg_insert(NarrationCache).values(
        user_id=user_id,
        metric_date=metric_date,
        signals_hash=h,
        narration_text=narration_text,
        model=settings.narration_model,
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["user_id", "metric_date", "signals_hash"]
    )
    await session.execute(stmt)
    if commit:
        await session.commit()
    else:
        await session.flush()

    log.info(
        "narration_generated",
        user_id=user_id,
        date=metric_date.isoformat(),
        model=settings.narration_model,
        chars=len(narration_text),
    )
    return narration_text
