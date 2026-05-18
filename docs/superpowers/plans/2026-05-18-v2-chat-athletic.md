# v2 — Chat Drawer + Athletic Visual Polish: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a right-side persistent chat drawer (Q&A + manual-log writes via Anthropic tool-use, SSE streaming, inline tool-confirmation) and redesign the dashboard to a Whoop/Athletic aesthetic — using HTML/CSS Hugo generates externally via Claude Design as the visual reference.

**Architecture:** Backend adds one new `/api/chat` SSE endpoint backed by 5 Anthropic tools (3 writes, 2 reads). Dashboard adds a new `ChatDrawer` component (sibling of `<main>` in the layout flex container), a new `HeroBlock` replacing `TodayStrip`, and restyles the existing tiles/charts to match the Whoop aesthetic. No DB schema changes; chat messages are session-scoped React state.

**Tech Stack:** FastAPI + Anthropic Python SDK (with streaming + tool-use), Next.js 16 + React 19 + Tailwind v4 (`@theme` block already established in Plan 3), `fetch` + `ReadableStream` for SSE consumption browser-side, Recharts unchanged.

**Spec reference:** `docs/superpowers/specs/2026-05-18-v2-chat-athletic-design.md` (in this repo).

**State at plan start:**
- Backend `growthink1/health-metrics-service` main HEAD `3c08004`, tag `v0.3.0-railway`
- Dashboard `growthink1/health-metrics-dashboard` main HEAD (post-Tailwind-v4 fix), tag `v0.1.0-dashboard-frontend`
- Live at `https://health.ironforgeai.com` behind Basic Auth
- 30 days of real data; HRV/RHR/Sleep tiles populated via the Whoop fallback shipped today
- Anthropic SDK already in `pyproject.toml` (`anthropic>=0.34.0`); used by `narration.py` for non-streaming, no tool-use

---

## File structure

**`health-metrics-service` additions:**
```
health-metrics-service/
├── src/health_metrics/
│   ├── chat_tools.py        # NEW — 5 tool definitions + handlers
│   ├── chat_prompts.py      # NEW — system prompt builder
│   └── routes/
│       └── chat.py          # NEW — POST /api/chat SSE endpoint
├── tests/
│   ├── test_chat_tools.py   # NEW — handler unit tests
│   ├── test_chat_prompts.py # NEW — snapshot test
│   └── test_chat_route.py   # NEW — SSE flow test with mocked Anthropic
```

**`health-metrics-dashboard` additions/changes:**
```
health-metrics-dashboard/
├── docs/inspiration/
│   └── v2-claude-design-output.html  # NEW — Hugo's Claude Design export, committed for reference
├── components/
│   ├── HeroBlock.tsx        # NEW — replaces TodayStrip
│   ├── MetricChip.tsx       # NEW — KPI strip chip
│   ├── ChatDrawer.tsx       # NEW — right-side persistent drawer
│   ├── ChatMessage.tsx      # NEW — single message bubble
│   ├── ChatToolUsePrompt.tsx # NEW — inline confirm/cancel card
│   ├── SparklineTile.tsx    # MODIFY — restyle to Whoop palette
│   ├── MetricChart.tsx      # MODIFY — restyle drilldown
│   └── WorkoutTable.tsx     # MODIFY — restyle workout rows
├── lib/
│   └── chat.ts              # NEW — useChatStream SSE hook
├── app/
│   ├── globals.css          # MODIFY — add athletic palette tokens to @theme block
│   ├── layout.tsx           # MODIFY — flex body with <main> + <ChatDrawer />
│   └── page.tsx             # MODIFY — swap TodayStrip → HeroBlock + MetricChip
├── tests/
│   ├── chat.test.ts         # NEW — Vitest for useChatStream SSE parser
│   └── e2e/v2.spec.ts       # NEW — Playwright chat Q&A + log-via-chat
```

Each file has one clear responsibility:
- `chat_tools.py` owns the 5 tool definitions (Anthropic format) + their handlers. Nothing in `routes/chat.py` knows what tools exist.
- `chat_prompts.py` builds the system prompt from DB state. No HTTP or streaming concerns.
- `routes/chat.py` orchestrates the SSE lifecycle: receive request → build prompt → stream from Anthropic → forward chunks → end at tool_use or done.
- `lib/chat.ts` is a single React hook returning `{messages, send, confirmToolUse, pendingToolUse, isStreaming}`. Consumers don't touch SSE parsing.
- `ChatDrawer.tsx` composes `ChatMessage` + `ChatToolUsePrompt` + the input box. No SSE knowledge.

---

## Task 1: Backend — `chat_tools.py` tool registry + handlers

**Files:**
- Create: `src/health_metrics/chat_tools.py`
- Create: `tests/test_chat_tools.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/test_chat_tools.py`:

```python
"""Chat tool handlers — 3 writes + 2 reads."""

from datetime import date

import pytest
from sqlalchemy import select, text

from health_metrics.chat_tools import (
    TOOL_DEFINITIONS,
    log_subjective,
    log_weight,
    log_nutrition,
    get_recent_metrics,
    get_workouts,
)
from health_metrics.models import ManualLog


def test_tool_definitions_shape():
    # 5 tools, each with name + description + input_schema
    assert len(TOOL_DEFINITIONS) == 5
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert names == {"log_subjective", "log_weight", "log_nutrition", "get_recent_metrics", "get_workouts"}
    for t in TOOL_DEFINITIONS:
        assert "name" in t and "description" in t and "input_schema" in t
        assert t["input_schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_log_weight_upserts(db_session, test_user_id):
    result = await log_weight(db_session, test_user_id, date(2026, 5, 17), 218.4)
    assert result["ok"] is True
    assert result["result"]["fields_updated"] == ["weight_lbs"]
    rows = (await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id)
    )).scalars().all()
    assert len(rows) == 1
    assert float(rows[0].weight_lbs) == 218.4


@pytest.mark.asyncio
async def test_log_subjective_partial_fields(db_session, test_user_id):
    result = await log_subjective(db_session, test_user_id, date(2026, 5, 17), energy=7, mood=8, hunger=None)
    assert result["ok"] is True
    assert set(result["result"]["fields_updated"]) == {"subjective_energy", "subjective_mood"}
    row = (await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id)
    )).scalar_one()
    assert row.subjective_energy == 7
    assert row.subjective_mood == 8
    assert row.subjective_hunger is None


@pytest.mark.asyncio
async def test_log_nutrition_upserts_macros(db_session, test_user_id):
    result = await log_nutrition(db_session, test_user_id, date(2026, 5, 17), kcal=2500, protein_g=180, fat_g=70, carbs_g=200)
    assert result["ok"] is True
    row = (await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id)
    )).scalar_one()
    assert row.kcal_consumed == 2500
    assert row.protein_g == 180


@pytest.mark.asyncio
async def test_log_weight_rejects_bad_date(db_session, test_user_id):
    result = await log_weight(db_session, test_user_id, "not-a-date", 218.0)
    assert result["ok"] is False
    assert "date" in result["error"].lower()


@pytest.mark.asyncio
async def test_get_recent_metrics_returns_json_list(db_session, test_user_id):
    # Seed 3 days
    await db_session.execute(text("""
        INSERT INTO daily_metrics (user_id, metric_date, oura_hrv_avg, whoop_day_strain, oura_status, whoop_status)
        VALUES (:u, '2026-05-15', 45, 10.0, 'ok', 'ok'),
               (:u, '2026-05-16', 47, 11.0, 'ok', 'ok'),
               (:u, '2026-05-17', 46, 13.0, 'ok', 'ok')
    """), {"u": test_user_id})
    await db_session.flush()

    result = await get_recent_metrics(db_session, test_user_id, days=7, anchor=date(2026, 5, 17))
    assert result["ok"] is True
    assert len(result["result"]["days"]) == 3
    last = result["result"]["days"][-1]
    assert last["date"] == "2026-05-17"
    assert last["hrv"] == 46
    assert last["strain"] == 13.0


@pytest.mark.asyncio
async def test_get_workouts_returns_list(db_session, test_user_id):
    await db_session.execute(text("""
        INSERT INTO workouts (user_id, workout_date, source, source_id, workout_type,
                              started_at, duration_min, strain)
        VALUES (:u, '2026-05-17', 'whoop', 'w-1', 'cycling',
                '2026-05-17T17:00:00+00:00'::timestamptz, 45, 12.5)
    """), {"u": test_user_id})
    await db_session.flush()
    result = await get_workouts(db_session, test_user_id, days=7, anchor=date(2026, 5, 17))
    assert result["ok"] is True
    assert len(result["result"]["workouts"]) == 1
    assert result["result"]["workouts"][0]["type"] == "cycling"
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
cd ~/code/health-metrics-service && source .venv/bin/activate
python3 -m pytest tests/test_chat_tools.py -xvs 2>&1 | tail -10
```
Expected: `ModuleNotFoundError: No module named 'health_metrics.chat_tools'`.

- [ ] **Step 1.3: Implement `chat_tools.py`**

Create `src/health_metrics/chat_tools.py`:

```python
"""Chat tool registry + handlers.

Five tools exposed to Anthropic for the /api/chat endpoint:
- 3 write tools (log_subjective, log_weight, log_nutrition) — upsert manual_log rows
- 2 read tools (get_recent_metrics, get_workouts) — fetch recent data for context

Write tools NEVER run server-side without a tool_confirmation: approved=True
arriving from the client. The chat route enforces that policy; this module
just exposes the handlers + definitions.
"""

from datetime import date as date_type, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DailyMetrics, ManualLog, Workout
from .routes.api import _read_metric  # reuse the Oura→Whoop fallback


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_recent_metrics",
        "description": "Read the user's daily health metrics (HRV, RHR, sleep, strain, recovery) for the last N days. Use this when the user asks about trends or compares their current state to recent history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 90, "description": "Number of days back from today"},
            },
            "required": ["days"],
        },
    },
    {
        "name": "get_workouts",
        "description": "Read the user's workout sessions for the last N days. Returns type, duration, strain, kcal per workout.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 90},
            },
            "required": ["days"],
        },
    },
    {
        "name": "log_subjective",
        "description": "Write the user's subjective ratings (energy, mood, hunger; each 1-10) for a given date into the manual_log table. The user MUST confirm before this runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO yyyy-mm-dd"},
                "energy": {"type": "integer", "minimum": 1, "maximum": 10},
                "mood": {"type": "integer", "minimum": 1, "maximum": 10},
                "hunger": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["date"],
        },
    },
    {
        "name": "log_weight",
        "description": "Write the user's weight (lbs) for a given date. The user MUST confirm before this runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO yyyy-mm-dd"},
                "weight_lbs": {"type": "number", "minimum": 50, "maximum": 500},
            },
            "required": ["date", "weight_lbs"],
        },
    },
    {
        "name": "log_nutrition",
        "description": "Write the user's nutrition (kcal + macros) for a given date. The user MUST confirm before this runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "kcal": {"type": "integer", "minimum": 0, "maximum": 10000},
                "protein_g": {"type": "integer", "minimum": 0, "maximum": 1000},
                "fat_g": {"type": "integer", "minimum": 0, "maximum": 1000},
                "carbs_g": {"type": "integer", "minimum": 0, "maximum": 2000},
            },
            "required": ["date"],
        },
    },
]


def _parse_date(d: Any) -> date_type | None:
    if isinstance(d, date_type):
        return d
    if isinstance(d, str):
        try:
            return date_type.fromisoformat(d)
        except ValueError:
            return None
    return None


async def _upsert_manual_log(
    session: AsyncSession, user_id: str, log_date: date_type, fields: dict[str, Any]
) -> dict[str, Any]:
    """Insert-or-update a manual_log row, returning which fields were updated."""
    stmt = (
        pg_insert(ManualLog)
        .values(user_id=user_id, log_date=log_date, **fields)
        .on_conflict_do_update(
            index_elements=["user_id", "log_date"],
            set_=fields,
        )
    )
    await session.execute(stmt)
    await session.commit()
    return {"fields_updated": list(fields.keys()), "logged_date": log_date.isoformat()}


async def log_subjective(
    session: AsyncSession, user_id: str, date: Any,
    energy: Optional[int] = None, mood: Optional[int] = None, hunger: Optional[int] = None,
) -> dict[str, Any]:
    d = _parse_date(date)
    if d is None:
        return {"ok": False, "error": f"invalid date: {date!r}"}
    fields: dict[str, Any] = {}
    if energy is not None:
        fields["subjective_energy"] = energy
    if mood is not None:
        fields["subjective_mood"] = mood
    if hunger is not None:
        fields["subjective_hunger"] = hunger
    if not fields:
        return {"ok": False, "error": "no fields to update (provide at least one of energy/mood/hunger)"}
    return {"ok": True, "result": await _upsert_manual_log(session, user_id, d, fields)}


async def log_weight(
    session: AsyncSession, user_id: str, date: Any, weight_lbs: float,
) -> dict[str, Any]:
    d = _parse_date(date)
    if d is None:
        return {"ok": False, "error": f"invalid date: {date!r}"}
    return {"ok": True, "result": await _upsert_manual_log(session, user_id, d, {"weight_lbs": weight_lbs})}


async def log_nutrition(
    session: AsyncSession, user_id: str, date: Any,
    kcal: Optional[int] = None, protein_g: Optional[int] = None,
    fat_g: Optional[int] = None, carbs_g: Optional[int] = None,
) -> dict[str, Any]:
    d = _parse_date(date)
    if d is None:
        return {"ok": False, "error": f"invalid date: {date!r}"}
    fields: dict[str, Any] = {}
    if kcal is not None:
        fields["kcal_consumed"] = kcal
    if protein_g is not None:
        fields["protein_g"] = protein_g
    if fat_g is not None:
        fields["fat_g"] = fat_g
    if carbs_g is not None:
        fields["carbs_g"] = carbs_g
    if not fields:
        return {"ok": False, "error": "no fields to update"}
    return {"ok": True, "result": await _upsert_manual_log(session, user_id, d, fields)}


async def get_recent_metrics(
    session: AsyncSession, user_id: str, days: int, anchor: date_type | None = None,
) -> dict[str, Any]:
    anchor = anchor or date_type.today()
    start = anchor - timedelta(days=days - 1)
    res = await session.execute(
        select(DailyMetrics)
        .where(DailyMetrics.user_id == user_id)
        .where(DailyMetrics.metric_date >= start)
        .where(DailyMetrics.metric_date <= anchor)
        .order_by(DailyMetrics.metric_date.asc())
    )
    rows = list(res.scalars().all())
    days_out = [
        {
            "date": r.metric_date.isoformat(),
            "hrv": _read_metric(r, "hrv"),
            "rhr": _read_metric(r, "rhr"),
            "sleep_min": _read_metric(r, "sleep_min"),
            "strain": _read_metric(r, "strain"),
            "recovery": _read_metric(r, "recovery"),
        }
        for r in rows
    ]
    return {"ok": True, "result": {"days": days_out}}


async def get_workouts(
    session: AsyncSession, user_id: str, days: int, anchor: date_type | None = None,
) -> dict[str, Any]:
    anchor = anchor or date_type.today()
    start = anchor - timedelta(days=days - 1)
    res = await session.execute(
        select(Workout)
        .where(Workout.user_id == user_id)
        .where(Workout.workout_date >= start)
        .where(Workout.workout_date <= anchor)
        .order_by(Workout.workout_date.asc())
    )
    workouts = [
        {
            "date": w.workout_date.isoformat(),
            "type": w.workout_type,
            "duration_min": w.duration_min,
            "strain": float(w.strain) if w.strain is not None else None,
            "kcal": w.kcal,
        }
        for w in res.scalars().all()
    ]
    return {"ok": True, "result": {"workouts": workouts}}


# Dispatch table — chat route looks up tool name → handler.
TOOL_HANDLERS = {
    "get_recent_metrics": get_recent_metrics,
    "get_workouts": get_workouts,
    "log_subjective": log_subjective,
    "log_weight": log_weight,
    "log_nutrition": log_nutrition,
}

READ_TOOLS = {"get_recent_metrics", "get_workouts"}
WRITE_TOOLS = {"log_subjective", "log_weight", "log_nutrition"}
```

- [ ] **Step 1.4: Run tests to verify pass**

```bash
python3 -m pytest tests/test_chat_tools.py -xvs 2>&1 | tail -15
```
Expected: 7 passed.

Then full sweep:
```bash
python3 -m pytest -q 2>&1 | tail -5
```
Expected: 51 passed (44 baseline + 7 new).

- [ ] **Step 1.5: Commit**

```bash
git add src/health_metrics/chat_tools.py tests/test_chat_tools.py
git commit -m "feat: chat tool registry + handlers (3 write + 2 read)"
git push
```

---

## Task 2: Backend — `chat_prompts.py` system prompt builder

**Files:**
- Create: `src/health_metrics/chat_prompts.py`
- Create: `tests/test_chat_prompts.py`

- [ ] **Step 2.1: Write the failing test**

Create `tests/test_chat_prompts.py`:

```python
"""System prompt builder — bakes today's recommendation + recent data into the seed."""

from datetime import date

import pytest
from sqlalchemy import text

from health_metrics.chat_prompts import build_system_prompt


@pytest.mark.asyncio
async def test_system_prompt_contains_recommendation_and_recent_data(db_session, test_user_id):
    # Seed 7 days of metrics with a deload-y profile
    await db_session.execute(text("""
        INSERT INTO daily_metrics (user_id, metric_date,
            oura_hrv_avg, oura_rhr, oura_sleep_duration_min,
            unified_hrv_z, unified_rhr_z, whoop_sleep_debt_min,
            whoop_day_strain, oura_status, whoop_status, ingestion_complete)
        VALUES
            (:u, '2026-05-11', 45, 60, 400, -1.0, 0.5, 200, 12.0, 'ok', 'ok', TRUE),
            (:u, '2026-05-12', 47, 58, 410, -0.8, 0.3, 180, 11.0, 'ok', 'ok', TRUE),
            (:u, '2026-05-13', 46, 59, 380, -1.2, 0.7, 220, 13.0, 'ok', 'ok', TRUE)
    """), {"u": test_user_id})
    await db_session.execute(text(
        "INSERT INTO manual_log (user_id, log_date, subjective_energy) VALUES (:u, '2026-05-13', 5)"
    ), {"u": test_user_id})
    await db_session.flush()

    prompt = await build_system_prompt(db_session, user_id=test_user_id, anchor=date(2026, 5, 13))

    # Contains the key sections we want Claude to see
    assert "health-metrics" in prompt.lower() or "recovery" in prompt.lower()
    assert "DELOAD" in prompt.upper() or "MAINTENANCE" in prompt.upper() or "DEFICIT" in prompt.upper()
    assert "2026-05-13" in prompt  # most recent day
    # The tools section
    assert "log_subjective" in prompt or "tools" in prompt.lower()


@pytest.mark.asyncio
async def test_system_prompt_handles_empty_db(db_session, test_user_id):
    # Empty DB for this user — should still produce a valid prompt, just without recent data
    prompt = await build_system_prompt(db_session, user_id=test_user_id, anchor=date(2026, 5, 13))
    assert isinstance(prompt, str)
    assert len(prompt) > 100  # has some content
```

- [ ] **Step 2.2: Run to verify failure**

```bash
python3 -m pytest tests/test_chat_prompts.py -xvs 2>&1 | tail -8
```
Expected: `ModuleNotFoundError: No module named 'health_metrics.chat_prompts'`.

- [ ] **Step 2.3: Implement `chat_prompts.py`**

Create `src/health_metrics/chat_prompts.py`:

```python
"""System prompt builder for /api/chat.

Seeds Claude with the user's current recommendation, recent metrics, and the
list of tools available. The prompt is built fresh per chat request from
live DB state — chat is not multi-turn-stateful on the backend (the client
sends the full message history each request).
"""

from datetime import date as date_type, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .chat_tools import TOOL_DEFINITIONS
from .regulation import compute_regulation_signals, regulate
from .routes.api import _read_metric
from .models import DailyMetrics
from sqlalchemy import select


async def build_system_prompt(
    session: AsyncSession,
    user_id: str,
    anchor: date_type | None = None,
) -> str:
    """Compose the system prompt for the /api/chat Anthropic call."""
    anchor = anchor or date_type.today()

    # 1. Today's recommendation
    signals = await compute_regulation_signals(session, user_id=user_id, anchor=anchor)
    rec = regulate(signals)

    # 2. Last 30 days of compact metrics
    start = anchor - timedelta(days=29)
    res = await session.execute(
        select(DailyMetrics)
        .where(DailyMetrics.user_id == user_id)
        .where(DailyMetrics.metric_date >= start)
        .where(DailyMetrics.metric_date <= anchor)
        .order_by(DailyMetrics.metric_date.asc())
    )
    rows = list(res.scalars().all())
    compact: list[dict[str, Any]] = []
    for r in rows:
        compact.append({
            "date": r.metric_date.isoformat(),
            "hrv": _read_metric(r, "hrv"),
            "rhr": _read_metric(r, "rhr"),
            "sleep_min": _read_metric(r, "sleep_min"),
            "strain": _read_metric(r, "strain"),
            "recovery": _read_metric(r, "recovery"),
        })

    tool_names = ", ".join(t["name"] for t in TOOL_DEFINITIONS)

    return f"""You are a recovery + training-readiness coach for the user of health-metrics, a personal Whoop + Oura analytics dashboard. The user is a single individual (Hugo); this is a private single-user tool.

Today is {anchor.isoformat()}.

Today's auto-regulation recommendation: {rec.recommendation.upper()}
- Suggested kcal: {rec.suggested_kcal}
- Training mod: {rec.suggested_training_mod}
- Rationale: {'; '.join(rec.rationale)}

Today's signals (relative to user baseline):
- HRV today: {compact[-1].get('hrv') if compact else 'no data'} ms
- HRV z (3-day avg): {signals.hrv_z_3d:.2f}σ
- RHR z (3-day avg): {signals.rhr_z_3d:.2f}σ
- Sleep debt: {signals.sleep_debt_min} min
- 7-day strain total: {signals.strain_7d_total:.1f}
- Subjective 3-day energy avg: {signals.subjective_3d_energy or 'unlogged'}

Recent 30 days (compact JSON, oldest first):
{compact}

Available tools: {tool_names}.

Behavior rules:
- Answer questions about the user's metrics, trends, and recovery state. Be concise and grounded in the numbers above; cite specific dates and values when useful.
- When the user asks to log something ('log my weight 218', 'energy was 7 today'), call the appropriate write tool (log_subjective / log_weight / log_nutrition). The user will see your tool call and confirm before it runs — you don't need to ask them to confirm in chat text; just call the tool with the right args.
- Default dates to today ({anchor.isoformat()}) unless the user specifies a different date.
- If the user asks for advice, give it briefly. You are a coach, not a doctor; if something looks medically concerning, suggest they talk to their physician.
- Use the read tools (get_recent_metrics, get_workouts) only if the data above doesn't cover what they're asking. The 30-day window is usually enough.
"""
```

- [ ] **Step 2.4: Run tests**

```bash
python3 -m pytest tests/test_chat_prompts.py -xvs 2>&1 | tail -10
```
Expected: 2 passed.

Full sweep: `python3 -m pytest -q | tail -5` → 53 passed.

- [ ] **Step 2.5: Commit**

```bash
git add src/health_metrics/chat_prompts.py tests/test_chat_prompts.py
git commit -m "feat: chat system-prompt builder seeds Claude with recent metrics + recommendation"
git push
```

---

## Task 3: Backend — `routes/chat.py` SSE endpoint

**Files:**
- Create: `src/health_metrics/routes/chat.py`
- Create: `tests/test_chat_route.py`

- [ ] **Step 3.1: Write the failing test**

Create `tests/test_chat_route.py`:

```python
"""POST /api/chat — SSE streaming. Test with a mocked Anthropic streaming client."""

from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


class _FakeAnthropicMessageStream:
    """Async iterator that yields scripted events. Pattern matches anthropic SDK shape:
    each event has a `.type` and event-specific fields."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for e in self._events:
            yield e


@pytest.mark.asyncio
async def test_chat_streams_plain_text_response(db_session, monkeypatch, test_user_id):
    # Patch the chat route's session factory + Anthropic client so we control both.
    from health_metrics.routes import chat as chat_route

    @asynccontextmanager
    async def _ctx():
        yield db_session
    monkeypatch.setattr(chat_route, "_session_factory", lambda: _ctx())

    # Fake Anthropic stream emits two text deltas then a message_stop
    fake_events = [
        type("E", (), {"type": "content_block_delta", "delta": type("D", (), {"type": "text_delta", "text": "Your HRV "})()})(),
        type("E", (), {"type": "content_block_delta", "delta": type("D", (), {"type": "text_delta", "text": "is low."})()})(),
        type("E", (), {"type": "message_stop"})(),
    ]

    fake_messages = AsyncMock()
    fake_messages.stream = lambda **kw: _FakeAnthropicMessageStream(fake_events)
    fake_client = AsyncMock()
    fake_client.messages = fake_messages
    monkeypatch.setattr(chat_route, "_build_anthropic_client", lambda: fake_client)

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/chat", json={
            "user_id": test_user_id,
            "messages": [{"role": "user", "content": "How am I?"}],
        })

    assert resp.status_code == 200
    body = resp.text
    # SSE events: data: {"type":"text","delta":"Your HRV "}
    assert 'data: {"type":"text","delta":"Your HRV "}' in body
    assert 'data: {"type":"text","delta":"is low."}' in body
    assert 'data: {"type":"done"}' in body


@pytest.mark.asyncio
async def test_chat_emits_tool_use_for_write_then_pauses(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import chat as chat_route

    @asynccontextmanager
    async def _ctx():
        yield db_session
    monkeypatch.setattr(chat_route, "_session_factory", lambda: _ctx())

    fake_events = [
        type("E", (), {"type": "content_block_start", "content_block": type("B", (), {
            "type": "tool_use", "id": "toolu_abc", "name": "log_weight", "input": {}
        })()})(),
        type("E", (), {"type": "content_block_delta", "delta": type("D", (), {
            "type": "input_json_delta", "partial_json": '{"date":"2026-05-17","weight_lbs":218}'
        })()})(),
        type("E", (), {"type": "content_block_stop"})(),
        type("E", (), {"type": "message_stop"})(),
    ]
    fake_messages = AsyncMock()
    fake_messages.stream = lambda **kw: _FakeAnthropicMessageStream(fake_events)
    fake_client = AsyncMock()
    fake_client.messages = fake_messages
    monkeypatch.setattr(chat_route, "_build_anthropic_client", lambda: fake_client)

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/chat", json={
            "user_id": test_user_id,
            "messages": [{"role": "user", "content": "log my weight 218"}],
        })

    assert resp.status_code == 200
    body = resp.text
    assert '"type":"tool_use"' in body
    assert '"name":"log_weight"' in body
    assert '"id":"toolu_abc"' in body
    assert '"weight_lbs":218' in body or '"weight_lbs": 218' in body


@pytest.mark.asyncio
async def test_chat_executes_write_after_confirmation(db_session, monkeypatch, test_user_id):
    """When client posts back with approved=True, the write executes via the tool handler."""
    from health_metrics.routes import chat as chat_route
    from health_metrics.models import ManualLog
    from sqlalchemy import select

    @asynccontextmanager
    async def _ctx():
        yield db_session
    monkeypatch.setattr(chat_route, "_session_factory", lambda: _ctx())

    # After confirmation, Anthropic is called again; mock it to just say "done"
    fake_events = [
        type("E", (), {"type": "content_block_delta", "delta": type("D", (), {"type": "text_delta", "text": "Done."})()})(),
        type("E", (), {"type": "message_stop"})(),
    ]
    fake_messages = AsyncMock()
    fake_messages.stream = lambda **kw: _FakeAnthropicMessageStream(fake_events)
    fake_client = AsyncMock()
    fake_client.messages = fake_messages
    monkeypatch.setattr(chat_route, "_build_anthropic_client", lambda: fake_client)

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/chat", json={
            "user_id": test_user_id,
            "messages": [
                {"role": "user", "content": "log my weight 218"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_abc", "name": "log_weight",
                     "input": {"date": "2026-05-17", "weight_lbs": 218}}
                ]},
            ],
            "tool_confirmation": {"id": "toolu_abc", "approved": True},
        })

    assert resp.status_code == 200
    assert 'data: {"type":"text","delta":"Done."}' in resp.text

    # Verify the write happened
    row = (await db_session.execute(
        select(ManualLog).where(ManualLog.user_id == test_user_id)
    )).scalar_one()
    assert float(row.weight_lbs) == 218
```

- [ ] **Step 3.2: Run tests to verify failure**

```bash
python3 -m pytest tests/test_chat_route.py -xvs 2>&1 | tail -10
```
Expected: `ModuleNotFoundError: No module named 'health_metrics.routes.chat'`.

- [ ] **Step 3.3: Implement `routes/chat.py`**

Create `src/health_metrics/routes/chat.py`:

```python
"""POST /api/chat — SSE streaming chat with Anthropic tool-use.

Lifecycle:
  client POST /api/chat {messages, [tool_confirmation]}
    ↓
  backend builds system prompt from DB state
    ↓
  if tool_confirmation.approved=True in the request, execute the write tool
  server-side first; append the tool_result to the messages history before
  calling Anthropic. (If approved=False, append a "user declined" tool_result.)
    ↓
  call anthropic.messages.stream(system=..., tools=..., messages=...)
    ↓
  for each event: emit SSE
    - text_delta → data: {"type":"text","delta":"..."}
    - tool_use stop → data: {"type":"tool_use","id":"...","name":"...","input":{...}}
    - message_stop → data: {"type":"done"}
"""

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog
from anthropic import AsyncAnthropic
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..chat_prompts import build_system_prompt
from ..chat_tools import TOOL_DEFINITIONS, TOOL_HANDLERS, WRITE_TOOLS
from ..config import get_settings
from ..db import AsyncSessionLocal

log = structlog.get_logger()
router = APIRouter(prefix="/api")


class ToolConfirmation(BaseModel):
    id: str
    approved: bool


class ChatRequest(BaseModel):
    user_id: str | None = None
    messages: list[dict[str, Any]]
    tool_confirmation: ToolConfirmation | None = None


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx():
        async with AsyncSessionLocal() as session:
            yield session
    return _ctx()


def _build_anthropic_client() -> AsyncAnthropic | None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


@router.post("/chat")
async def chat(req: ChatRequest):
    settings = get_settings()
    uid = req.user_id or settings.user_id
    client = _build_anthropic_client()

    async def gen():
        if client is None:
            yield _sse({"type": "error", "message": "ANTHROPIC_API_KEY not configured"})
            yield _sse({"type": "done"})
            return

        async with _session_factory() as session:
            # 1. Handle confirmation: execute write tool + append tool_result to history
            messages = list(req.messages)
            if req.tool_confirmation is not None:
                # Find the matching tool_use block in the latest assistant message
                tool_use_block = _find_tool_use(messages, req.tool_confirmation.id)
                if tool_use_block is None:
                    yield _sse({"type": "error", "message": f"tool_use_id {req.tool_confirmation.id} not found"})
                    yield _sse({"type": "done"})
                    return

                if req.tool_confirmation.approved:
                    handler = TOOL_HANDLERS.get(tool_use_block["name"])
                    if handler is None:
                        result = {"ok": False, "error": f"unknown tool {tool_use_block['name']}"}
                    else:
                        try:
                            result = await handler(session, uid, **tool_use_block["input"])
                        except Exception as e:
                            log.exception("chat_tool_handler_failed", tool=tool_use_block["name"])
                            result = {"ok": False, "error": str(e)}
                else:
                    result = {"ok": False, "error": "user declined"}

                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": req.tool_confirmation.id,
                        "content": json.dumps(result),
                        "is_error": not result.get("ok", False),
                    }],
                })

            # 2. Build the system prompt + call Anthropic
            system = await build_system_prompt(session, uid)
            try:
                async with client.messages.stream(
                    model=settings.narration_model,
                    system=system,
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                    max_tokens=2048,
                ) as stream:
                    current_tool_use: dict[str, Any] | None = None
                    current_tool_json = ""
                    async for event in stream:
                        etype = getattr(event, "type", None)
                        if etype == "content_block_start":
                            block = getattr(event, "content_block", None)
                            if block is not None and getattr(block, "type", None) == "tool_use":
                                current_tool_use = {
                                    "id": block.id, "name": block.name, "input": dict(block.input) if block.input else {},
                                }
                                current_tool_json = ""
                        elif etype == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            dtype = getattr(delta, "type", None)
                            if dtype == "text_delta":
                                yield _sse({"type": "text", "delta": delta.text})
                            elif dtype == "input_json_delta":
                                current_tool_json += delta.partial_json
                        elif etype == "content_block_stop":
                            if current_tool_use is not None:
                                # Finalize tool_use input from accumulated JSON
                                if current_tool_json:
                                    try:
                                        current_tool_use["input"] = json.loads(current_tool_json)
                                    except json.JSONDecodeError:
                                        pass
                                yield _sse({"type": "tool_use", **current_tool_use})
                                current_tool_use = None
                                current_tool_json = ""
                        elif etype == "message_stop":
                            break
            except Exception as e:
                log.exception("chat_anthropic_failed")
                yield _sse({"type": "error", "message": str(e)})
            yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")


def _find_tool_use(messages: list[dict[str, Any]], tool_use_id: str) -> dict[str, Any] | None:
    """Walk back through messages to find the tool_use block with matching id."""
    for msg in reversed(messages):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                    return block
    return None
```

- [ ] **Step 3.4: Mount the router in `main.py`**

Open `src/health_metrics/main.py`. Add the import + include alongside the existing routers:

```python
from .routes.chat import router as chat_router
# ...
app.include_router(chat_router)
```

- [ ] **Step 3.5: Run tests**

```bash
python3 -m pytest tests/test_chat_route.py -xvs 2>&1 | tail -10
```
Expected: 3 passed.

Full sweep: 56 passed.

- [ ] **Step 3.6: Commit**

```bash
git add src/health_metrics/routes/chat.py tests/test_chat_route.py src/health_metrics/main.py
git commit -m "feat: POST /api/chat SSE streaming endpoint with Anthropic tool-use"
git push
```

---

## Task 4: Hugo runs Claude Design externally (gate)

This is a non-coding step. The implementer should pause and ping Hugo with:

> "Backend chat endpoint is ready. Before I touch dashboard styling, please run the Claude Design prompt from the spec (section: 'Claude Design prompt') in claude.ai/design or your preferred design assistant. Save the output HTML to `~/code/health-metrics-dashboard/docs/inspiration/v2-claude-design-output.html` and commit it. Tell me 'design ready' when done."

- [ ] **Step 4.1: Hugo runs the Claude Design prompt**

The full prompt is in the spec file (this is the literal text Hugo pastes; the spec keeps the canonical copy).

- [ ] **Step 4.2: Hugo saves the output**

```bash
mkdir -p ~/code/health-metrics-dashboard/docs/inspiration
# Save Claude Design's HTML output to:
# ~/code/health-metrics-dashboard/docs/inspiration/v2-claude-design-output.html
cd ~/code/health-metrics-dashboard
git add docs/inspiration/v2-claude-design-output.html
git commit -m "design: Claude Design output for v2 athletic redesign"
git push
```

- [ ] **Step 4.3: Confirm with implementer**

Hugo pings: "design ready". Implementer resumes with T5.

---

## Task 5: Dashboard — extract palette + class tokens into Tailwind v4 `@theme`

**Files:**
- Modify: `app/globals.css`
- Reference: `docs/inspiration/v2-claude-design-output.html`

- [ ] **Step 5.1: Read the Claude Design output**

Open `docs/inspiration/v2-claude-design-output.html`. Extract:
- Any new color CSS variables not already in `globals.css`'s `@theme` block (e.g. additional accent shades, gradient stops)
- The recovery-score color logic (red <33 / amber 33-66 / green >66) as a small comment block

- [ ] **Step 5.2: Update `app/globals.css`**

The existing `@theme` block has the Console palette. Add (preserving existing tokens):

```css
@theme {
  /* Existing Console palette tokens — keep all of these */
  --color-bg: #0a0e14;
  --color-surface: #0e1422;
  --color-border: #1f3a5f;
  --color-text: #d4d4d4;
  --color-text-muted: #5a8db5;
  --color-accent-primary: #7eb5e0;
  --color-accent-warm: #e2a04a;
  --color-accent-good: #5ad4a8;
  --color-accent-bad: #e25a4a;
  --color-accent-strain: #d44a8a;

  /* v2 Athletic additions — recovery score banding + hero gradient stops */
  --color-recovery-low: #ff3a5e;      /* recovery < 33 */
  --color-recovery-mid: #e2a04a;      /* recovery 33-66 */
  --color-recovery-high: #5ad4a8;     /* recovery > 66 */
  --color-hero-grad-start: #1a0f1f;
  --color-hero-grad-end: #0a0e14;
}
```

If the Claude Design output uses additional named tokens not above, add them here with matching `--color-*` names so Tailwind utilities generate (e.g. `bg-recovery-low`).

Also expose recovery-band vars as plain `:root` vars for direct `style={{color: 'var(--recovery-low)'}}` usage in components that compute the band dynamically:

```css
:root {
  /* ... existing vars ... */
  --recovery-low: #ff3a5e;
  --recovery-mid: #e2a04a;
  --recovery-high: #5ad4a8;
  --hero-grad-start: #1a0f1f;
}
```

- [ ] **Step 5.3: Build to verify**

```bash
cd ~/code/health-metrics-dashboard
npm run build 2>&1 | tail -10
```
Expected: build succeeds. CSS bundle includes the new classes:
```bash
CSS=$(find .next -name '*.css' -path '*static*' | head -1)
grep -oE 'recovery-low|recovery-mid|recovery-high|hero-grad' "$CSS" | sort -u
```
Expected: at least `recovery-low recovery-mid recovery-high hero-grad-start` present.

- [ ] **Step 5.4: Commit**

```bash
git add app/globals.css
git commit -m "feat: extend @theme with athletic palette tokens for v2 redesign"
git push
```

---

## Task 6: Dashboard — `HeroBlock` + `MetricChip` (replaces TodayStrip)

**Files:**
- Create: `components/HeroBlock.tsx`
- Create: `components/MetricChip.tsx`
- Modify: `app/page.tsx`

- [ ] **Step 6.1: Implement `MetricChip.tsx`**

Create `components/MetricChip.tsx`:

```tsx
interface Props {
  label: string;
  value: string;
  delta?: string;
  color: "primary" | "warm" | "good" | "bad" | "strain";
}

const COLOR_MAP: Record<Props["color"], string> = {
  primary: "border-accent-primary text-accent-primary",
  warm: "border-accent-warm text-accent-warm",
  good: "border-accent-good text-accent-good",
  bad: "border-accent-bad text-accent-bad",
  strain: "border-accent-strain text-accent-strain",
};

export function MetricChip({ label, value, delta, color }: Props) {
  const classes = COLOR_MAP[color];
  return (
    <div className="flex flex-col items-center gap-0.5 px-3 py-2 border-l-2 bg-surface min-w-[88px]" >
      <div className={`text-[9px] uppercase tracking-widest font-mono ${classes.split(" ")[1]}`}>
        {label}
      </div>
      <div className="font-mono text-lg font-bold text-text">{value}</div>
      {delta ? <div className="text-[9px] text-text-muted">{delta}</div> : null}
    </div>
  );
}
```

- [ ] **Step 6.2: Implement `HeroBlock.tsx`**

Create `components/HeroBlock.tsx`:

```tsx
import type { TodayStripData } from "@/lib/types";
import { recommendationLabel } from "@/lib/format";

interface Props {
  data: TodayStripData;
  metricDate: string;
  recoveryScore: number | null;  // for the ring; 0-100
}

function _recoveryColor(score: number | null): string {
  if (score === null) return "var(--text-muted)";
  if (score < 33) return "var(--recovery-low)";
  if (score < 66) return "var(--recovery-mid)";
  return "var(--recovery-high)";
}

function RecoveryRing({ score }: { score: number | null }) {
  const display = score === null ? "—" : `${score}`;
  const color = _recoveryColor(score);
  const dashArray = 2 * Math.PI * 60;  // r=60, circumference
  const dashOffset = score === null ? dashArray : dashArray * (1 - score / 100);
  return (
    <svg width="140" height="140" viewBox="0 0 140 140" className="shrink-0">
      <circle cx="70" cy="70" r="60" fill="none" stroke="var(--border)" strokeWidth="6" />
      <circle
        cx="70" cy="70" r="60" fill="none"
        stroke={color} strokeWidth="6" strokeLinecap="round"
        strokeDasharray={dashArray} strokeDashoffset={dashOffset}
        transform="rotate(-90 70 70)"
        style={{ transition: "stroke-dashoffset 600ms ease-out" }}
      />
      <text
        x="70" y="70" textAnchor="middle" dominantBaseline="central"
        fill="var(--text)" fontSize="34" fontWeight="800"
        fontFamily="Inter, system-ui, sans-serif"
      >
        {display}
      </text>
      <text
        x="70" y="98" textAnchor="middle" dominantBaseline="central"
        fill="var(--text-muted)" fontSize="9" letterSpacing="2"
        fontFamily="JetBrains Mono, monospace"
      >
        RECOVERY
      </text>
    </svg>
  );
}

export function HeroBlock({ data, metricDate, recoveryScore }: Props) {
  const recommendation = recommendationLabel(data.recommendation).toUpperCase();
  return (
    <div
      className="flex gap-6 items-center p-6 rounded border border-border"
      style={{
        background: "linear-gradient(135deg, var(--hero-grad-start) 0%, var(--bg) 100%)",
      }}
    >
      <RecoveryRing score={recoveryScore} />
      <div className="flex flex-col gap-2">
        <div className="text-[10px] font-mono uppercase tracking-widest text-text-muted">
          {metricDate}
        </div>
        <div className="text-4xl font-extrabold text-text leading-none tracking-tight">
          {recommendation}
        </div>
        {data.suggested_training_mod ? (
          <div className="text-sm text-text-muted">{data.suggested_training_mod}</div>
        ) : null}
        {data.suggested_kcal !== null ? (
          <div className="text-xs font-mono text-text-muted">
            Target: <span className="text-text">{data.suggested_kcal.toLocaleString()} kcal</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
```

- [ ] **Step 6.3: Modify `app/page.tsx` to use the new components**

Replace the TodayStrip import + usage. Open `app/page.tsx`:

```tsx
import { HeroBlock } from "@/components/HeroBlock";
import { MetricChip } from "@/components/MetricChip";
import { NarrationLine } from "@/components/NarrationLine";
import { LogPanel } from "@/components/LogPanel";
import { SparklineTile } from "@/components/SparklineTile";
import { WindowSelector } from "@/components/WindowSelector";
import { fetchDashboardToday, fetchDashboardGrid } from "@/lib/api";
import { formatHours } from "@/lib/format";

export const dynamic = "force-dynamic";

interface PageProps { searchParams: Promise<{ days?: string }> }

export default async function GridPage({ searchParams }: PageProps) {
  const { days: daysParam } = await searchParams;
  const days = Number(daysParam ?? 14);
  const [today, grid] = await Promise.all([
    fetchDashboardToday(),
    fetchDashboardGrid("hugo", days),
  ]);

  const recoveryTile = grid.tiles.find((t) => t.metric === "recovery");
  const recoveryScore = recoveryTile?.current ?? null;
  const hrvTile = grid.tiles.find((t) => t.metric === "hrv");
  const rhrTile = grid.tiles.find((t) => t.metric === "rhr");
  const sleepTile = grid.tiles.find((t) => t.metric === "sleep_min");
  const strainTile = grid.tiles.find((t) => t.metric === "strain");

  return (
    <div className="space-y-5 max-w-5xl">
      <HeroBlock
        data={today.today_strip}
        metricDate={today.metric_date}
        recoveryScore={recoveryScore as number | null}
      />

      <div className="flex flex-wrap gap-2">
        <MetricChip label="HRV" value={hrvTile?.current !== null ? String(hrvTile?.current ?? "—") : "—"} color="primary" />
        <MetricChip label="RHR" value={rhrTile?.current !== null ? String(rhrTile?.current ?? "—") : "—"} color="warm" />
        <MetricChip label="Sleep" value={sleepTile?.current !== null ? formatHours(sleepTile?.current ?? null) : "—"} color="good" />
        <MetricChip label="Strain" value={strainTile?.current !== null ? String(strainTile?.current ?? "—") : "—"} color="strain" />
      </div>

      <NarrationLine narration={today.narration} />
      <LogPanel logDate={today.metric_date} logStatus={today.today_strip.log_status} />

      <div className="flex items-center justify-between">
        <div className="text-xs text-text-muted font-mono">Window:</div>
        <WindowSelector defaultDays={days} />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {grid.tiles.map((tile) => (
          <SparklineTile key={tile.metric} tile={tile} />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 6.4: Build + smoke**

```bash
npm run build 2>&1 | tail -8
```
Expected: build clean.

Start dev + smoke:
```bash
nohup npm run dev > /tmp/v2_smoke.log 2>&1 & disown
sleep 6
curl -s -u 'hugo:<DASHBOARD_PASSWORD>' http://localhost:3000/ > /tmp/v2.html
grep -oE 'DELOAD|MAINTENANCE|RECOVERY|RECOMMEND' /tmp/v2.html | sort -u
pkill -f 'next-server\|next dev' 2>/dev/null
```
Expected: at least `DELOAD` + `RECOVERY` present.

- [ ] **Step 6.5: Commit**

```bash
git add components/HeroBlock.tsx components/MetricChip.tsx app/page.tsx
git commit -m "feat: HeroBlock + MetricChip replace TodayStrip (athletic redesign)"
git push
```

---

## Task 7: Dashboard — restyle existing tiles + chart + table

**Files:**
- Modify: `components/SparklineTile.tsx`
- Modify: `components/MetricChart.tsx`
- Modify: `components/WorkoutTable.tsx`

- [ ] **Step 7.1: Restyle `SparklineTile.tsx`**

Update the tile's classNames to give it a top accent stripe + bigger hero number. Open `components/SparklineTile.tsx`. Replace the JSX inside the `<Link>` (keep the imports, `COLORS`, `LABELS`, `formatCurrent`, and `isStrain` logic):

```tsx
  return (
    <Link
      href={`/metric/${tile.metric}`}
      className="block border border-border rounded-md bg-surface hover:border-accent-primary hover:shadow-[0_4px_12px_rgba(0,0,0,0.4)] transition overflow-hidden"
    >
      <div className="h-1 w-full" style={{ backgroundColor: color }} />
      <div className="p-4">
        <div className="flex items-baseline justify-between mb-3">
          <div className="text-[10px] uppercase tracking-widest font-mono text-text-muted">{label}</div>
          <div className="font-mono text-2xl font-bold text-text">{formatCurrent(tile.metric, tile.current)}</div>
        </div>
        <div className="h-14">
          <ResponsiveContainer width="100%" height="100%">
            {isStrain ? (
              <BarChart data={data}>
                <Bar dataKey="value" fill={color} radius={[2, 2, 0, 0]} />
              </BarChart>
            ) : (
              <LineChart data={data}>
                <Line type="monotone" dataKey="value" stroke={color} strokeWidth={2.5} dot={false} />
              </LineChart>
            )}
          </ResponsiveContainer>
        </div>
      </div>
    </Link>
  );
```

- [ ] **Step 7.2: Restyle `MetricChart.tsx`**

Open `components/MetricChart.tsx`. The chart structure stays; tweak axis tick fontFamily for monospace, stroke weight up, dot size up:

Find the `<XAxis tick={...}>` line. Update both XAxis and YAxis tick props in both the BarChart and LineChart branches to:

```tsx
            <XAxis dataKey="date" tick={{ fill: "var(--text-muted)", fontSize: 10, fontFamily: "JetBrains Mono, monospace" }} />
            <YAxis tick={{ fill: "var(--text-muted)", fontSize: 10, fontFamily: "JetBrains Mono, monospace" }} />
```

And in the LineChart branch, the `<Line>` element:

```tsx
            <Line type="monotone" dataKey="value" stroke={color} strokeWidth={2.5} dot={{ r: 4, fill: color, strokeWidth: 0 }} activeDot={{ r: 6 }} />
```

- [ ] **Step 7.3: Restyle `WorkoutTable.tsx`**

Open `components/WorkoutTable.tsx`. Add hover state + a color stripe on the strain column. Replace the entire `<tr>` body block inside `{workouts.map((w) => (...))}` with:

```tsx
            <tr key={`${w.source}-${w.source_id}`} className="border-t border-border hover:bg-surface/60 transition">
              <td className="px-3 py-2 text-text-muted">{w.date}</td>
              <td className="px-3 py-2 text-text">{w.type ?? "—"}</td>
              <td className="px-3 py-2 text-right">{w.duration_min}m</td>
              <td className="px-3 py-2 text-right">
                <span className="font-bold" style={{ color: "var(--accent-strain)" }}>
                  {w.strain?.toFixed(1) ?? "—"}
                </span>
              </td>
              <td className="px-3 py-2 text-right">{w.kcal ?? "—"}</td>
              <td className="px-3 py-2 text-right text-text-muted">{w.avg_hr ?? "—"}</td>
              <td className="px-3 py-2 text-right text-text-muted">{w.max_hr ?? "—"}</td>
            </tr>
```

- [ ] **Step 7.4: Build + smoke**

```bash
npm run build 2>&1 | tail -8
```
Expected: build clean.

- [ ] **Step 7.5: Commit**

```bash
git add components/SparklineTile.tsx components/MetricChart.tsx components/WorkoutTable.tsx
git commit -m "feat: restyle tiles + chart + workout table to athletic palette"
git push
```

---

## Task 8: Dashboard — `lib/chat.ts` SSE hook

**Files:**
- Create: `lib/chat.ts`
- Create: `tests/chat.test.ts`

- [ ] **Step 8.1: Write the failing test**

Create `tests/chat.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useChatStream } from "@/lib/chat";

function makeSseResponse(events: string[]) {
  // events is array of JSON strings; produce a Response with a streaming body
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const e of events) {
        controller.enqueue(encoder.encode(`data: ${e}\n\n`));
      }
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("useChatStream", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("accumulates text deltas into the last assistant message", async () => {
    globalThis.fetch = vi.fn(async () => makeSseResponse([
      JSON.stringify({ type: "text", delta: "Your HRV " }),
      JSON.stringify({ type: "text", delta: "is low." }),
      JSON.stringify({ type: "done" }),
    ])) as typeof fetch;

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      result.current.send("How am I?");
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[0].role).toBe("user");
    expect(result.current.messages[1].role).toBe("assistant");
    expect(result.current.messages[1].content).toBe("Your HRV is low.");
  });

  it("surfaces WRITE tool_use events as pendingToolUse", async () => {
    globalThis.fetch = vi.fn(async () => makeSseResponse([
      JSON.stringify({
        type: "tool_use",
        id: "toolu_abc",
        name: "log_weight",
        input: { date: "2026-05-17", weight_lbs: 218 },
      }),
      JSON.stringify({ type: "done" }),
    ])) as typeof fetch;

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      result.current.send("log my weight 218");
    });
    await waitFor(() => expect(result.current.pendingToolUse).not.toBeNull());

    expect(result.current.pendingToolUse?.name).toBe("log_weight");
    expect(result.current.pendingToolUse?.input).toEqual({ date: "2026-05-17", weight_lbs: 218 });
  });

  it("auto-confirms READ tool_use without showing pendingToolUse, re-fetches with tool_confirmation", async () => {
    const fetchSpy = vi.fn();
    // First call: stream a read tool_use
    fetchSpy.mockResolvedValueOnce(makeSseResponse([
      JSON.stringify({
        type: "tool_use", id: "toolu_read", name: "get_workouts", input: { days: 7 },
      }),
      JSON.stringify({ type: "done" }),
    ]));
    // Second call (auto-confirm): stream the resume text
    fetchSpy.mockResolvedValueOnce(makeSseResponse([
      JSON.stringify({ type: "text", delta: "Looked at workouts." }),
      JSON.stringify({ type: "done" }),
    ]));
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      result.current.send("how many workouts this week?");
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));

    expect(result.current.pendingToolUse).toBeNull();    // read tool did NOT pause for UI
    expect(fetchSpy).toHaveBeenCalledTimes(2);            // first stream + auto-confirm round-trip
    // Second fetch body should include tool_confirmation
    const secondBody = JSON.parse(fetchSpy.mock.calls[1][1]!.body as string);
    expect(secondBody.tool_confirmation).toEqual({ id: "toolu_read", approved: true });
  });
});
```

- [ ] **Step 8.2: Run to verify failure**

```bash
cd ~/code/health-metrics-dashboard
npm run test 2>&1 | tail -10
```
Expected: error importing `@/lib/chat`.

- [ ] **Step 8.3: Implement `lib/chat.ts`**

Create `lib/chat.ts`:

```ts
"use client";

import { useCallback, useRef, useState } from "react";

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
  toolUse?: { id: string; name: string; input: Record<string, unknown> };
  toolResult?: { id: string; approved: boolean; result?: unknown };
}

export interface ToolUsePrompt {
  id: string;
  name: string;
  input: Record<string, unknown>;
}

// Read tools auto-execute without prompting the user. Write tools (anything
// starting with `log_`) always require an inline Yes/No confirmation.
const READ_TOOL_NAMES = new Set(["get_recent_metrics", "get_workouts"]);

interface UseChatStreamReturn {
  messages: ChatMessage[];
  pendingToolUse: ToolUsePrompt | null;
  isStreaming: boolean;
  error: string | null;
  send: (text: string) => void;
  confirmToolUse: (approved: boolean) => void;
  cancel: () => void;
}

export function useChatStream(): UseChatStreamReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pendingToolUse, setPendingToolUse] = useState<ToolUsePrompt | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const runStream = useCallback(async (body: Record<string, unknown>, baseMessages: ChatMessage[]) => {
    setIsStreaming(true);
    setError(null);
    const ac = new AbortController();
    abortRef.current = ac;

    // Append an empty assistant placeholder we'll accumulate into
    const placeholder: ChatMessage = { role: "assistant", content: "" };
    setMessages([...baseMessages, placeholder]);

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: ac.signal,
      });
      if (!resp.ok || !resp.body) {
        setError(`Chat failed: ${resp.status}`);
        setIsStreaming(false);
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let acc = "";
      let toolUse: ToolUsePrompt | null = null;
      let done = false;
      while (!done) {
        const { value, done: eof } = await reader.read();
        if (eof) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          if (!chunk.startsWith("data:")) continue;
          let event;
          try {
            event = JSON.parse(chunk.slice(5).trim());
          } catch {
            continue;
          }
          if (event.type === "text") {
            acc += event.delta;
            setMessages((m) => {
              const copy = [...m];
              copy[copy.length - 1] = { role: "assistant", content: acc };
              return copy;
            });
          } else if (event.type === "tool_use") {
            toolUse = { id: event.id, name: event.name, input: event.input };
            // Only PROMPT for write tools; read tools auto-confirm after the stream ends.
            if (!READ_TOOL_NAMES.has(toolUse.name)) {
              setPendingToolUse(toolUse);
            }
          } else if (event.type === "error") {
            setError(event.message);
          } else if (event.type === "done") {
            done = true;
            break;
          }
        }
      }

      // After the stream ends: if it ended on a READ tool_use, auto-confirm
      // and re-stream WITHOUT user interaction.
      if (toolUse !== null && READ_TOOL_NAMES.has(toolUse.name)) {
        const continued: ChatMessage[] = [
          ...baseMessages,
          { role: "assistant", content: acc, toolUse },
        ];
        setMessages(continued);
        // Recursive call — fire-and-forget; runStream sets isStreaming on entry.
        await runStream(
          {
            messages: continued.map(toApi),
            tool_confirmation: { id: toolUse.id, approved: true },
          },
          continued,
        );
        return;  // runStream will reset isStreaming when done
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, []);

  const send = useCallback((text: string) => {
    const newMessages: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(newMessages);
    void runStream(
      { messages: newMessages.map(toApi) },
      newMessages,
    );
  }, [messages, runStream]);

  const confirmToolUse = useCallback((approved: boolean) => {
    if (!pendingToolUse) return;
    const tu = pendingToolUse;
    setPendingToolUse(null);
    // Append the tool_use as part of the assistant turn, then trigger the
    // backend with tool_confirmation. Backend appends the tool_result.
    const continued: ChatMessage[] = [...messages];
    // The last assistant message already has the text; add the toolUse marker.
    const lastIdx = continued.length - 1;
    if (lastIdx >= 0 && continued[lastIdx].role === "assistant") {
      continued[lastIdx] = { ...continued[lastIdx], toolUse: tu };
    }
    void runStream(
      {
        messages: continued.map(toApi),
        tool_confirmation: { id: tu.id, approved },
      },
      continued,
    );
  }, [messages, pendingToolUse, runStream]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { messages, pendingToolUse, isStreaming, error, send, confirmToolUse, cancel };
}

function toApi(m: ChatMessage): { role: string; content: unknown } {
  if (m.toolUse) {
    return {
      role: "assistant",
      content: [
        ...(m.content ? [{ type: "text", text: m.content }] : []),
        { type: "tool_use", id: m.toolUse.id, name: m.toolUse.name, input: m.toolUse.input },
      ],
    };
  }
  return { role: m.role, content: m.content };
}
```

- [ ] **Step 8.4: Run tests**

```bash
npm run test 2>&1 | tail -8
```
Expected: 7 passed (5 existing + 2 new).

- [ ] **Step 8.5: Commit**

```bash
git add lib/chat.ts tests/chat.test.ts
git commit -m "feat: useChatStream SSE hook for /api/chat consumption"
git push
```

---

## Task 9: Dashboard — `ChatDrawer` + sub-components + layout mount

**Files:**
- Create: `components/ChatDrawer.tsx`
- Create: `components/ChatMessage.tsx`
- Create: `components/ChatToolUsePrompt.tsx`
- Modify: `app/layout.tsx`

- [ ] **Step 9.1: Implement `ChatMessage.tsx`**

Create `components/ChatMessage.tsx`:

```tsx
"use client";

import type { ChatMessage as ChatMessageType } from "@/lib/chat";

export function ChatMessage({ message, isStreaming }: { message: ChatMessageType; isStreaming?: boolean }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-2`}>
      <div
        className={
          isUser
            ? "max-w-[85%] rounded-lg bg-accent-primary/10 border border-accent-primary/30 text-text px-3 py-2 text-sm"
            : "max-w-[85%] rounded-lg bg-surface border border-border text-text px-3 py-2 text-sm"
        }
      >
        {message.content}
        {isStreaming && !isUser ? <span className="inline-block w-2 h-3 ml-1 bg-accent-primary animate-pulse" /> : null}
      </div>
    </div>
  );
}
```

- [ ] **Step 9.2: Implement `ChatToolUsePrompt.tsx`**

Create `components/ChatToolUsePrompt.tsx`:

```tsx
"use client";

import type { ToolUsePrompt } from "@/lib/chat";

interface Props {
  tool: ToolUsePrompt;
  onConfirm: () => void;
  onCancel: () => void;
}

function describe(tool: ToolUsePrompt): string {
  const args = Object.entries(tool.input)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
  return `${tool.name}(${args})`;
}

export function ChatToolUsePrompt({ tool, onConfirm, onCancel }: Props) {
  const isWrite = tool.name.startsWith("log_");
  return (
    <div className="my-2 p-3 border border-accent-warm rounded-lg bg-accent-warm/10">
      <div className="text-[10px] uppercase font-mono tracking-widest text-accent-warm mb-1">
        {isWrite ? "Claude wants to write" : "Claude wants to call"}
      </div>
      <div className="text-xs font-mono text-text mb-3 break-words">{describe(tool)}</div>
      <div className="flex gap-2">
        <button
          onClick={onConfirm}
          className="px-3 py-1 text-xs font-mono uppercase tracking-wider border border-accent-good text-accent-good rounded hover:bg-accent-good/10 transition"
        >
          Confirm
        </button>
        <button
          onClick={onCancel}
          className="px-3 py-1 text-xs font-mono uppercase tracking-wider border border-border text-text-muted rounded hover:border-accent-bad hover:text-accent-bad transition"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 9.3: Implement `ChatDrawer.tsx`**

Create `components/ChatDrawer.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { useChatStream } from "@/lib/chat";
import { ChatMessage } from "./ChatMessage";
import { ChatToolUsePrompt } from "./ChatToolUsePrompt";

const STORAGE_KEY = "chat:drawer:state";

export function ChatDrawer() {
  const [collapsed, setCollapsed] = useState(false);
  const [input, setInput] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);
  const { messages, pendingToolUse, isStreaming, error, send, confirmToolUse } = useChatStream();

  // Persist collapsed state
  useEffect(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
    if (saved === "collapsed") setCollapsed(true);
  }, []);
  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem(STORAGE_KEY, collapsed ? "collapsed" : "open");
    }
  }, [collapsed]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, pendingToolUse]);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim();
    if (!t || isStreaming) return;
    setInput("");
    send(t);
  }

  if (collapsed) {
    return (
      <div
        className="shrink-0 w-8 border-l border-border bg-surface flex items-center justify-center cursor-pointer hover:bg-bg transition"
        onClick={() => setCollapsed(false)}
        title="Open Claude chat"
      >
        <div
          className="font-mono uppercase tracking-widest text-xs text-text-muted"
          style={{ writingMode: "vertical-rl", textOrientation: "mixed" }}
        >
          ASK CLAUDE
        </div>
      </div>
    );
  }

  return (
    <aside className="shrink-0 w-[360px] border-l border-border bg-bg flex flex-col h-screen sticky top-0">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-surface">
        <div className="font-mono uppercase tracking-widest text-xs text-accent-primary">
          Claude
        </div>
        <button
          onClick={() => setCollapsed(true)}
          className="text-text-muted hover:text-text text-xs font-mono"
          title="Collapse"
        >
          ✕
        </button>
      </div>
      <div ref={listRef} className="flex-1 overflow-y-auto px-3 py-3">
        {messages.length === 0 ? (
          <div className="text-xs text-text-muted italic">
            Ask about your data, or say things like &quot;log my weight 218&quot; or
            &quot;energy was 7 today&quot;.
          </div>
        ) : null}
        {messages.map((m, i) => (
          <div key={i}>
            <ChatMessage
              message={m}
              isStreaming={isStreaming && i === messages.length - 1 && m.role === "assistant"}
            />
            {m.toolUse ? (
              <div className="text-[10px] text-text-muted font-mono mb-2 ml-1">
                ✓ {m.toolUse.name}(…)
              </div>
            ) : null}
          </div>
        ))}
        {pendingToolUse ? (
          <ChatToolUsePrompt
            tool={pendingToolUse}
            onConfirm={() => confirmToolUse(true)}
            onCancel={() => confirmToolUse(false)}
          />
        ) : null}
        {error ? (
          <div className="my-2 p-2 border border-accent-bad rounded text-xs text-accent-bad font-mono">
            {error}
          </div>
        ) : null}
      </div>
      <form onSubmit={onSubmit} className="border-t border-border p-2 flex gap-2 bg-surface">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask Claude…"
          className="flex-1 bg-bg border border-border rounded px-2 py-1.5 text-sm text-text focus:outline-none focus:border-accent-primary"
          disabled={isStreaming}
        />
        <button
          type="submit"
          disabled={isStreaming || !input.trim()}
          className="px-3 py-1.5 text-xs font-mono uppercase tracking-wider border border-accent-primary text-accent-primary rounded hover:bg-accent-primary/10 disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          {isStreaming ? "…" : "Send"}
        </button>
      </form>
    </aside>
  );
}
```

- [ ] **Step 9.4: Mount in `app/layout.tsx`**

Open `app/layout.tsx`. Change the body wrapper into a flex container with the drawer as a sibling of `<main>`:

```tsx
import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { NavHeader } from "@/components/NavHeader";
import { ChatDrawer } from "@/components/ChatDrawer";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const jetbrainsMono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "health-metrics",
  description: "Hugo's personal health metrics dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="min-h-screen bg-bg text-text flex flex-col">
        <NavHeader />
        <div className="flex flex-1">
          <main className="flex-1 p-6 overflow-x-hidden">{children}</main>
          <ChatDrawer />
        </div>
      </body>
    </html>
  );
}
```

- [ ] **Step 9.5: Build + manual smoke**

```bash
npm run build 2>&1 | tail -8
```
Expected: build clean (TypeScript + ESLint).

```bash
nohup npm run dev > /tmp/v2_smoke.log 2>&1 & disown
sleep 6
curl -s -u 'hugo:<DASHBOARD_PASSWORD>' http://localhost:3000/ > /tmp/d.html
grep -oE 'ASK CLAUDE|Claude|Ask Claude' /tmp/d.html | sort -u
pkill -f 'next-server\|next dev' 2>/dev/null
```
Expected: `Claude` and either `ASK CLAUDE` or `Ask Claude` (depending on collapsed state default; either is fine).

- [ ] **Step 9.6: Commit**

```bash
git add components/ChatDrawer.tsx components/ChatMessage.tsx components/ChatToolUsePrompt.tsx app/layout.tsx
git commit -m "feat: ChatDrawer with tool-use confirmation UI mounted in layout"
git push
```

---

## Task 10: Dashboard — Playwright e2e for v2

**Files:**
- Create: `tests/e2e/v2.spec.ts`

- [ ] **Step 10.1: Write the e2e test**

Create `tests/e2e/v2.spec.ts`:

```ts
import { test, expect } from "@playwright/test";

test.describe("v2 — hero + chat", () => {
  test("hero block renders with recovery ring + uppercase recommendation", async ({ page }) => {
    await page.goto("/");
    // Hero recommendation uppercase
    const heroRec = page.getByText(/DELOAD|DEFICIT|MAINTENANCE/);
    await expect(heroRec).toBeVisible();
    // Recovery ring should be an SVG containing "RECOVERY" label
    await expect(page.locator("svg text", { hasText: "RECOVERY" })).toBeVisible();
  });

  test("chat drawer is visible and has an input box", async ({ page }) => {
    await page.goto("/");
    // The drawer might be open or collapsed; try to find input or the rail text
    const input = page.getByPlaceholder(/Ask Claude/i);
    const rail = page.getByText(/ASK CLAUDE/i);
    // At least one must be visible
    const inputVisible = await input.isVisible().catch(() => false);
    const railVisible = await rail.isVisible().catch(() => false);
    expect(inputVisible || railVisible).toBe(true);
  });
});
```

- [ ] **Step 10.2: Run e2e (locally, with backend up)**

Backend must be at localhost:8000 + dashboard at localhost:3000:

```bash
# Backend
cd ~/code/health-metrics-service && source .venv/bin/activate && nohup uvicorn src.health_metrics.main:app --port 8000 > /tmp/be.log 2>&1 & disown
sleep 4

# Dashboard
cd ~/code/health-metrics-dashboard && pkill -f 'next-server\|next dev' 2>/dev/null
sleep 1
nohup npm run dev > /tmp/fe.log 2>&1 & disown
sleep 6

npm run e2e 2>&1 | tail -20
pkill -f 'next-server\|next dev\|uvicorn src.health_metrics' 2>/dev/null
```
Expected: 5 tests pass (3 existing + 2 new).

- [ ] **Step 10.3: Commit**

```bash
git add tests/e2e/v2.spec.ts
git commit -m "test: Playwright e2e for hero block + chat drawer"
git push
```

---

## Task 11: Deploy + production smoke

**Files:** None new — just deploys.

- [ ] **Step 11.1: Deploy backend**

```bash
cd ~/code/health-metrics-service
RAILWAY_CALLER="skill:use-railway@1.2.1" RAILWAY_AGENT_SESSION="railway-skill-v2-$(date +%s)" \
  railway up --service backend --detach
```

Wait for build (~90s). Check:
```bash
sleep 90
curl -s -u 'hugo:<DASHBOARD_PASSWORD>' -X POST -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hi"}]}' \
  'https://health.ironforgeai.com/api/chat' | head -c 200
```
Expected: SSE event lines starting with `data: {"type":"text",...}`.

- [ ] **Step 11.2: Deploy dashboard**

```bash
cd ~/code/health-metrics-dashboard
RAILWAY_CALLER="skill:use-railway@1.2.1" RAILWAY_AGENT_SESSION="railway-skill-v2-$(date +%s)" \
  railway up --service dashboard --detach
```

Wait for build, then visit `https://health.ironforgeai.com` in a browser. Confirm:
- Hero block with recovery ring + uppercase recommendation
- KPI chip strip below hero
- 6 sparkline tiles with top accent stripes
- Right-side drawer with "Ask Claude" input
- Send "hi" → assistant response streams in
- Send "log my weight 218" → tool-use confirm card appears → click Confirm → row lands in `manual_log`

- [ ] **Step 11.3: Tag releases**

```bash
cd ~/code/health-metrics-service
git tag -a v0.4.0-chat -m "v2 chat endpoint + tool-use"
git push --tags

cd ~/code/health-metrics-dashboard
git tag -a v0.2.0-athletic-chat -m "v2 — athletic redesign + ChatDrawer"
git push --tags
```

---

## Validation checklist (v2 exit criteria)

- [ ] Backend: `python3 -m pytest -q` → 56 passed (44 baseline + 12 new across T1/T2/T3)
- [ ] Dashboard: `npm run test` → 7 passed (5 existing + 2 new chat tests)
- [ ] Dashboard: `npm run build` clean (TypeScript + ESLint)
- [ ] Dashboard: `npm run e2e` → 5 tests pass
- [ ] `https://health.ironforgeai.com` hero block renders with recovery ring + uppercase recommendation
- [ ] Chat drawer appears on the right; collapses to "ASK CLAUDE" rail; localStorage persists state
- [ ] Send "hi" in chat → assistant streams a response
- [ ] Send "log my weight 218 for today" → tool-use confirm card appears → Confirm → DB row exists in `manual_log`
- [ ] Cancel button on tool-use card → no DB write
- [ ] Tags pushed: `v0.4.0-chat` (backend), `v0.2.0-athletic-chat` (dashboard)

## What's NOT in this plan (deferred to v3)

- Chat message persistence across sessions (would add a `chat_messages` table)
- Voice input
- Image upload (multimodal logging)
- Compare-period view, workout overlays, export, mobile-first responsive
- Audit log of every tool-use write
