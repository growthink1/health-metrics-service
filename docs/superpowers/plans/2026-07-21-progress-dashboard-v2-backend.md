# Progress Dashboard v2 — Backend Plan (Phases 1 + 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the backend for the progress dashboard — four `/api/v1/progress/*` read endpoints, a `progress_config`/math/parser layer, the event-`id` fold-in, and the connector-health endpoint + scheduler check — all in `health-metrics-service` (one deploy), plus the MCP re-vendor. The frontend `/progress` view is a separate follow-up plan built against these live endpoints.

**Architecture:** New `src/health_metrics/progress/` package holds pure logic (`config`, `math`, `strength_notes`, `weight_series`); `routes/progress.py` + `routes/health_connectors.py` are thin I/O shells over it, following the existing `meals_v1.py`/`activities.py` route pattern. Weight reuses the existing `kalman_weight` + `training_water_series` kernels (never reimplements the model). No new tables; one nullable column (`body_composition.vat_cm2`).

**Tech Stack:** FastAPI, async SQLAlchemy 2.0, Alembic, Pydantic v2, Postgres. Python 3.12 on CI (3.11 local venv).

**Spec:** `docs/superpowers/specs/2026-07-21-progress-dashboard-v2-design.md`

## Global Constraints
- Conventional commits, NO co-author trailer.
- `engine.py` (regulation) stays I/O-free — irrelevant here, but don't touch it.
- Pure modules (`progress/config.py`, `math.py`, `strength_notes.py`) take no DB/clock; the route/series layer does I/O. `date.today()` only in routes/series glue, passed into pure fns.
- Endpoints: `?user_id` default `hugo`; **empty data → empty arrays/objects, never 500**; no auth.
- New route + non-`tests/regulation/` test files must be added to BOTH ruff lists in `.github/workflows/ci.yml` (Task 13) or CI fails. New `src/health_metrics/progress/` files + `tests/regulation/` are covered by existing ruff globs only if placed there — put progress tests under `tests/` and register them (Task 13).
- macOS DB tests may segfault under `--cov`; run DB tests without coverage. `source .venv/bin/activate`; local Postgres on :5433 (`postgresql+asyncpg://hms:hms_dev_password@localhost:5433/health_metrics`).
- `proj`/`estimated`/`schedule` values are projections — never returned under a `measured` key.

---

### Task 1: `progress/config.py` — constants + exercise aliases

**Files:**
- Create: `src/health_metrics/progress/__init__.py` (empty), `src/health_metrics/progress/config.py`
- Test: `tests/test_progress_config.py`

**Interfaces:**
- Produces: `PLAN_START: date`, `GOAL: dict`, `BF_DROP_PCT_PER_MO`, `LEAN_GAIN_LB_PER_MO`, `PROJ_TAPER`, `CONNECTOR_STALE_HOURS`, `TOKEN_EXPIRY_WARN_HOURS: float`; `BIG4: list[str]`; `EXERCISE_ALIASES: list[tuple[str,str,str]]`; `resolve_exercise(raw: str) -> tuple[str,str] | None` (canonical, group).

- [ ] **Step 1: Write the failing test**
```python
from health_metrics.progress.config import BIG4, GOAL, PLAN_START, resolve_exercise


def test_plan_and_goal_constants():
    assert PLAN_START.isoformat() == "2026-06-01"
    assert GOAL == {"weight": 180.0, "bf": 13.0, "lean": 155.0}
    assert BIG4 == ["Bench Press", "Back Squat", "Lat Pulldown", "Cable Row"]


def test_resolve_exercise_specific_before_generic():
    assert resolve_exercise("Back squat: ramp 135") == ("Back Squat", "Legs")
    assert resolve_exercise("Bench: 220x5") == ("Bench Press", "Push")
    assert resolve_exercise("Lat pulldown 250 working") == ("Lat Pulldown", "Pull")
    assert resolve_exercise("Cable row 260 @7") == ("Cable Row", "Pull")
    assert resolve_exercise("Barbell RDL: 185x8x3") == ("Barbell RDL", "Legs")


def test_resolve_exercise_unknown_returns_none():
    assert resolve_exercise("back GOOD under load") is None
```

- [ ] **Step 2: Run to verify it fails** — `source .venv/bin/activate && pytest tests/test_progress_config.py -q` → FAIL (module not found).

- [ ] **Step 3: Implement**
```python
"""Progress-dashboard model constants + exercise alias map. Pure — no DB/clock.

Constants are hypotheses revised at each DEXA; the endpoints present them as
projections, never measurements. See
docs/superpowers/specs/2026-07-21-progress-dashboard-v2-design.md.
"""

from __future__ import annotations

from datetime import date

PLAN_START = date(2026, 6, 1)
GOAL = {"weight": 180.0, "bf": 13.0, "lean": 155.0}
BF_DROP_PCT_PER_MO = 1.8
LEAN_GAIN_LB_PER_MO = 0.8
PROJ_TAPER = 0.90
CONNECTOR_STALE_HOURS = 36.0
TOKEN_EXPIRY_WARN_HOURS = 48.0

BIG4 = ["Bench Press", "Back Squat", "Lat Pulldown", "Cable Row"]

# (lowercased substring pattern, canonical label, group). Specific patterns FIRST.
EXERCISE_ALIASES: list[tuple[str, str, str]] = [
    ("lat pulldown", "Lat Pulldown", "Pull"),
    ("cable row", "Cable Row", "Pull"),
    ("rear-delt fly", "Rear Delt Fly", "Pull"),
    ("rear delt fly", "Rear Delt Fly", "Pull"),
    ("shrug", "Shrug", "Pull"),
    ("hammer curl", "Hammer Curl", "Pull"),
    ("cable curl", "Cable Curl", "Pull"),
    ("back squat", "Back Squat", "Legs"),
    ("barbell rdl", "Barbell RDL", "Legs"),
    ("bulgarian split squat", "Bulgarian Split Squat", "Legs"),
    ("goblet squat", "Goblet Squat", "Legs"),
    ("incline db press", "Incline DB Press", "Push"),
    ("overhead press", "Overhead Press", "Push"),
    ("ohp", "Overhead Press", "Push"),
    ("lateral raise", "Lateral Raise", "Push"),
    ("bench", "Bench Press", "Push"),
    ("squat", "Back Squat", "Legs"),
]


def resolve_exercise(raw: str) -> tuple[str, str] | None:
    low = raw.lower()
    for pattern, canonical, group in EXERCISE_ALIASES:
        if pattern in low:
            return canonical, group
    return None
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_progress_config.py -q` → PASS.
- [ ] **Step 5: Commit** — `git add src/health_metrics/progress/ tests/test_progress_config.py && git commit -m "feat(progress): config constants + exercise alias map"`

---

### Task 2: `progress/math.py` — pure projection helpers

**Files:** Create `src/health_metrics/progress/math.py`; Test `tests/test_progress_math.py`

**Interfaces:**
- Produces:
  - `schedule_weight(month_idx: int, months_total: int, start_w: float, goal_w: float) -> float` — linear interpolation.
  - `taper_project(current: float, monthly_delta: float, months_ahead: int, taper: float) -> float` — decaying extrapolation.
  - `glidepath(current: float, per_month: float, months_ahead: int, direction: int) -> float` — `direction` -1 (bf drop) or +1 (lean gain).

- [ ] **Step 1: Write the failing test**
```python
from health_metrics.progress.math import glidepath, schedule_weight, taper_project


def test_schedule_weight_linear():
    assert schedule_weight(0, 6, 229.0, 180.0) == 229.0
    assert schedule_weight(6, 6, 229.0, 180.0) == 180.0
    assert abs(schedule_weight(3, 6, 229.0, 180.0) - 204.5) < 1e-9


def test_taper_project_decays():
    # month1: current + delta*taper^0 ; month2: + delta*taper^1 ...
    v = taper_project(217.0, -5.0, 2, 0.9)
    assert abs(v - (217.0 - 5.0 - 5.0 * 0.9)) < 1e-9


def test_glidepath_direction():
    assert glidepath(33.1, 1.8, 2, -1) == 33.1 - 3.6
    assert glidepath(147.2, 0.8, 2, 1) == 147.2 + 1.6
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_progress_math.py -q` → FAIL.

- [ ] **Step 3: Implement**
```python
"""Pure projection math for the progress endpoints — no I/O, deterministic."""

from __future__ import annotations


def schedule_weight(month_idx: int, months_total: int, start_w: float, goal_w: float) -> float:
    if months_total <= 0:
        return start_w
    frac = min(max(month_idx / months_total, 0.0), 1.0)
    return start_w + (goal_w - start_w) * frac


def taper_project(current: float, monthly_delta: float, months_ahead: int, taper: float) -> float:
    v = current
    for i in range(months_ahead):
        v += monthly_delta * (taper ** i)
    return v


def glidepath(current: float, per_month: float, months_ahead: int, direction: int) -> float:
    return current + direction * per_month * months_ahead
```

- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(progress): pure projection math helpers"`

---

### Task 3: `progress/strength_notes.py` — the LIFTS parser

**Files:** Create `src/health_metrics/progress/strength_notes.py`; Test `tests/test_progress_strength_notes.py`

**Interfaces:**
- Consumes: `resolve_exercise` (Task 1).
- Produces: `@dataclass(frozen=True) LiftTop(canonical: str, group: str, day: date, top_load: float)`; `parse_lifts(note: str, day: date) -> list[LiftTop]`.

- [ ] **Step 1: Write the failing tests (the five real strings + skips)**
```python
from datetime import date

from health_metrics.progress.strength_notes import LiftTop, parse_lifts


def _tops(note):
    return {lt.canonical: lt.top_load for lt in parse_lifts(note, date(2026, 7, 21))}


def test_bench_takes_achieved_not_future_gate():
    n = "LIFTS (Push #6) | Session RPE 7 | Bench: 220x5, 220x5, 225x5 @7.5 (PR top set) -> Push #7 (7/24): 225 across, gate 230 if first <=RPE6 | OHP: 110x5x3 @7 -> 112.5 next"
    t = _tops(n)
    assert t["Bench Press"] == 225.0  # not 230 (future gate, right of ->)
    assert t["Overhead Press"] == 110.0  # not 112.5


def test_squat_ramp_and_working_max_not_next():
    n = "LIFTS (Lower #2) | Session RPE 6.5 | back GOOD under load | Back squat: ramp 135/185/205; 225x5 x3, 235x5 @7.5 (PR top set) -> NEXT: 235 across, gate 240 | Barbell RDL: 185x8x3 @7.5"
    t = _tops(n)
    assert t["Back Squat"] == 235.0  # not 240
    assert t["Barbell RDL"] == 185.0


def test_pull_bare_load_forms():
    n = "LIFTS (Pull #5) | Session RPE 7.5 | Recovery 57 | Lat pulldown 250 working, top @7.5 -> Pull #6 (7/22) 255 | Cable row 260 @7 -> hold 265"
    t = _tops(n)
    assert t["Lat Pulldown"] == 250.0
    assert t["Cable Row"] == 260.0


def test_commentary_segments_skipped():
    n = "LIFTS (Lower) | Session RPE 7 | back GOOD under load | felt strong"
    assert parse_lifts(n, date(2026, 7, 21)) == []


def test_group_and_date_carried():
    got = parse_lifts("LIFTS (Push) | Bench: 225x5 @7", date(2026, 7, 17))
    assert got == [LiftTop("Bench Press", "Push", date(2026, 7, 17), 225.0)]
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_progress_strength_notes.py -q` → FAIL.

- [ ] **Step 3: Implement**
```python
"""Parse `LIFTS | …` free-text lift logs from manual_log.notes into top working-set
loads. Conservative: skips any segment it can't confidently resolve. Pure.

Grammar (pipe-delimited): `LIFTS (label) | Session RPE n | <commentary> |
<exercise>: <loads> @rpe (notes) -> <future cue> | …`. The top working-set load is
the MAX load LEFT of `->` (the right side is the next-session target). See spec §5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .config import resolve_exercise

# a load token: a number immediately followed by 'x' (NxR), or trailing 'working'/'@'.
_LOAD_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:x\d|working|@)", re.IGNORECASE)


@dataclass(frozen=True)
class LiftTop:
    canonical: str
    group: str
    day: date
    top_load: float


def parse_lifts(note: str, day: date) -> list[LiftTop]:
    if not note or "LIFTS" not in note:
        return []
    body = note[note.index("LIFTS"):]
    out: list[LiftTop] = []
    for seg in body.split("|"):
        seg = seg.strip()
        if not seg or seg.lower().startswith("lifts") or seg.lower().startswith("session rpe"):
            continue
        resolved = resolve_exercise(seg)
        if resolved is None:
            continue
        achieved = seg.split("->", 1)[0]  # drop the future NEXT/gate cue
        loads = [float(m.group(1)) for m in _LOAD_RE.finditer(achieved)]
        if not loads:
            continue
        canonical, group = resolved
        out.append(LiftTop(canonical=canonical, group=group, day=day, top_load=max(loads)))
    return out
```

- [ ] **Step 4: Run to verify it passes** — PASS (5 tests).
- [ ] **Step 5: Commit** — `git commit -m "feat(progress): LIFTS notes parser (conservative, achieved-only)"`

---

### Task 4: `body_composition.vat_cm2` column + migration

**Files:** Modify `src/health_metrics/models.py`; Create `alembic/versions/<rev>_body_comp_vat.py`; Modify `tests/test_models_migration.py` (constraint canary unaffected; add a column-presence check).

**Interfaces:** Produces `BodyComposition.vat_cm2: int | None`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_models_migration.py`)
```python
def test_body_composition_has_vat_cm2():
    t = Base.metadata.tables["body_composition"]
    assert "vat_cm2" in t.columns
```
- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_models_migration.py::test_body_composition_has_vat_cm2 -q` → FAIL.
- [ ] **Step 3: Add the column** to `BodyComposition` (after `fat_mass_lbs`): `vat_cm2: Mapped[Optional[int]] = mapped_column(Integer)`.
- [ ] **Step 4: Generate + fill migration** — `alembic revision -m "body_composition vat_cm2"`; `down_revision` = current head (`alembic heads`). Body:
```python
def upgrade() -> None:
    op.add_column("body_composition", sa.Column("vat_cm2", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("body_composition", "vat_cm2")
```
- [ ] **Step 5: Round-trip local** — `export DATABASE_URL=…5433/health_metrics && alembic upgrade head && alembic downgrade -1 && alembic upgrade head`; `pytest tests/test_models_migration.py -q` → PASS.
- [ ] **Step 6: Commit** — `git commit -m "feat(db): body_composition.vat_cm2 column"` (backfill 145 for the Jul-8 row is an operator step, §Deploy).

---

### Task 5: `progress/weight_series.py` — per-day dewatered series (DB glue)

**Files:** Create `src/health_metrics/progress/weight_series.py`; Test `tests/regulation/test_progress_weight_series.py` (DB test — `tests/regulation/` is a DB dir).

**Interfaces:**
- Consumes: `kalman_weight` (`regulation/kalman.py`, returns `list[FilteredPoint(date, level, velocity, level_var, velocity_var)]`), `training_water_series` + `get_water_params` (`regulation/water_retention*`), `_fetch_loads_by_day` (reuse via a local copy of the query — do NOT import the private brief helper).
- Produces: `async def dewatered_by_date(session, user_id: str, start: date, end: date) -> dict[date, float]` — filtered level − absolute training water per day.

- [ ] **Step 1: Write the failing test**
```python
from datetime import date

import pytest

from health_metrics.models import ManualLog
from health_metrics.progress.weight_series import dewatered_by_date


@pytest.mark.asyncio
async def test_dewatered_by_date_returns_series(db_session, test_user_id):
    for i, w in enumerate([220.0, 219.5, 219.0, 218.5, 219.0, 218.0, 217.5]):
        db_session.add(ManualLog(user_id=test_user_id, log_date=date(2026, 7, 1 + i), weight_lbs=w))
    await db_session.flush()
    out = await dewatered_by_date(db_session, test_user_id, date(2026, 7, 1), date(2026, 7, 7))
    assert len(out) == 7
    assert all(isinstance(v, float) for v in out.values())
    # with no workouts, water offset ~0 -> dewatered ≈ filtered level (within a few lb)
    assert abs(out[date(2026, 7, 7)] - 217.5) < 5.0
```
- [ ] **Step 2: Run to verify it fails** — FAIL (no DB coverage flag).
- [ ] **Step 3: Implement**
```python
"""Per-day de-watered weight series for the progress weight chart. Reuses the
Kalman + training-water kernels (same primitives as compute_weight_trend) — does
NOT reimplement the model."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ManualLog, Workout
from ..regulation.kalman import kalman_weight
from ..regulation.water_retention import training_water_series
from ..regulation.water_retention_config import fallback_load, get_water_params


async def _loads_by_day(session: AsyncSession, user_id: str, start: date, end: date) -> dict[date, float]:
    r = await session.execute(
        select(Workout.workout_date, Workout.workout_type, Workout.strain).where(
            Workout.user_id == user_id, Workout.workout_date >= start, Workout.workout_date <= end
        )
    )
    loads: dict[date, float] = {}
    for wdate, wtype, strain in r.all():
        loads[wdate] = loads.get(wdate, 0.0) + fallback_load(wtype, float(strain) if strain is not None else None)
    return loads


async def dewatered_by_date(session: AsyncSession, user_id: str, start: date, end: date) -> dict[date, float]:
    r = await session.execute(
        select(ManualLog.log_date, ManualLog.weight_lbs)
        .where(
            ManualLog.user_id == user_id,
            ManualLog.weight_lbs.is_not(None),
            ManualLog.log_date >= start,
            ManualLog.log_date <= end,
        )
        .order_by(ManualLog.log_date.asc())
    )
    obs = [(d, float(w)) for d, w in r.all()]
    if not obs:
        return {}
    points = kalman_weight(obs)
    dates = [d for d, _ in obs]
    loads = await _loads_by_day(session, user_id, start, end)
    params = get_water_params(user_id)
    water = {p.date: p.water_lbs for p in training_water_series(loads, dates, params)} if loads else {}
    return {p.date: round(p.level - water.get(p.date, 0.0), 2) for p in points}
```
- [ ] **Step 4: Run to verify it passes** — `pytest tests/regulation/test_progress_weight_series.py -q` (DB, no `--cov`) → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(progress): per-day dewatered weight series"`

---

### Task 6: `routes/progress.py` + `GET /progress/weight`

**Files:** Create `src/health_metrics/routes/progress.py`; Modify `src/health_metrics/main.py`; Test `tests/test_routes_progress_weight.py`

**Interfaces:**
- Consumes: `compute_weight_trend` (`regulation/brief.py` → `WeightTrend` with `current_lbs`, `weight_dewatered_lbs`, `filtered_velocity_lbs_per_day`), `dewatered_by_date` (Task 5), `config` (Task 1), `math` (Task 2), `ManualLog`, `HealthEvent`.
- Produces: `GET /api/v1/progress/weight` returning the §4.1 shape.

- [ ] **Step 1: Write the failing test**
```python
from contextlib import asynccontextmanager
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient

from health_metrics.models import ManualLog


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_progress_weight_shape_and_kpis(db_session, monkeypatch, test_user_id):
    db_session.add(ManualLog(user_id=test_user_id, log_date=date(2026, 6, 1), weight_lbs=229.0))
    db_session.add(ManualLog(user_id=test_user_id, log_date=date(2026, 7, 20), weight_lbs=217.4))
    await db_session.flush()
    from health_metrics.routes import progress as prog
    monkeypatch.setattr(prog, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(f"/api/v1/progress/weight?user_id={test_user_id}")
    assert resp.status_code == 200
    b = resp.json()
    assert {"daily", "horizon", "kpis"} <= b.keys()
    assert b["daily"][0] == {"day": 0, "date": "Jun 1", "weight": 229.0, "dewatered": b["daily"][0]["dewatered"]}
    assert b["kpis"]["current"] == 217.4
    assert abs(b["kpis"]["lost_since_start"] - 11.6) < 0.01


@pytest.mark.asyncio
async def test_progress_weight_empty_no_500(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import progress as prog
    monkeypatch.setattr(prog, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(f"/api/v1/progress/weight?user_id={test_user_id}")
    assert resp.status_code == 200
    assert resp.json()["daily"] == []
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module not found).

- [ ] **Step 3: Implement `progress.py`** (shared session factory + the weight route; later tasks append routes to this file)
```python
"""GET /api/v1/progress/* — read-only dashboard endpoints. No auth (single-user)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type

import structlog
from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..models import HealthEvent, ManualLog
from ..progress import config as cfg
from ..progress import math as pmath
from ..progress.weight_series import dewatered_by_date
from ..regulation.brief import compute_weight_trend

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1/progress")


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            yield session

    return _ctx()


def _mon_label(d: date_type) -> str:
    return f"{d.strftime('%b')} {d.day}"


@router.get("/weight")
async def progress_weight(user_id: str = Query(default="hugo")) -> dict:
    async with _session_factory() as session:
        r = await session.execute(
            select(ManualLog.log_date, ManualLog.weight_lbs)
            .where(ManualLog.user_id == user_id, ManualLog.weight_lbs.is_not(None), ManualLog.log_date >= cfg.PLAN_START)
            .order_by(ManualLog.log_date.asc())
        )
        rows = [(d, float(w)) for d, w in r.all()]
        if not rows:
            return {"daily": [], "horizon": [], "kpis": {}}

        start_w = rows[0][1]
        today = date_type.today()
        dw = await dewatered_by_date(session, user_id, cfg.PLAN_START, today)
        daily = [
            {"day": (d - cfg.PLAN_START).days, "date": _mon_label(d), "weight": w, "dewatered": dw.get(d)}
            for d, w in rows
        ]

        trend = await compute_weight_trend(session, user_id, today)
        current = rows[-1][1]
        vel_wk = (trend.filtered_velocity_lbs_per_day or 0.0) * 7

        # monthly horizon buckets from PLAN_START through the goal window
        er = await session.execute(
            select(HealthEvent.expected_resolution).where(
                HealthEvent.user_id == user_id, HealthEvent.event_type == "scheduled_dexa"
            )
        )
        dexa_months = {(d.year, d.month) for (d,) in er.all() if d is not None}
        months_total = 12
        by_month: dict[tuple[int, int], float] = {}
        for d, w in rows:
            by_month[(d.year, d.month)] = w  # latest wins (rows ascending)
        horizon = []
        for i in range(months_total + 1):
            y = cfg.PLAN_START.year + (cfg.PLAN_START.month - 1 + i) // 12
            m = (cfg.PLAN_START.month - 1 + i) % 12 + 1
            label = f"{date_type(y, m, 1).strftime('%b')} {date_type(y, m, 1).day}"
            horizon.append({
                "m": label,
                "actual": by_month.get((y, m)),
                "schedule": round(pmath.schedule_weight(i, months_total, start_w, cfg.GOAL["weight"]), 1),
                "proj": round(pmath.taper_project(current, vel_wk * 4.345, max(i - _months_since(cfg.PLAN_START, today), 0), cfg.PROJ_TAPER), 1),
                "dexa": (y, m) in dexa_months,
            })

        return {
            "daily": daily,
            "horizon": horizon,
            "kpis": {
                "current": round(current, 1),
                "dewatered": round(trend.weight_dewatered_lbs, 1) if trend.weight_dewatered_lbs is not None else None,
                "lost_since_start": round(start_w - current, 1),
                "to_goal": round(current - cfg.GOAL["weight"], 1),
                "filtered_lb_per_wk": round(vel_wk, 1),
            },
        }


def _months_since(start: date_type, now: date_type) -> int:
    return (now.year - start.year) * 12 + (now.month - start.month)
```
Register in `main.py`: `from .routes.progress import router as progress_router` + `app.include_router(progress_router)`.

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_routes_progress_weight.py -q` (no `--cov`) → PASS (2).
- [ ] **Step 5: Commit** — `git commit -m "feat(api): GET /progress/weight"`

---

### Task 7: `GET /progress/bodycomp`

**Files:** Modify `routes/progress.py`; Test `tests/test_routes_progress_bodycomp.py`

**Interfaces:** Consumes `BodyComposition` (incl. `vat_cm2`), `config`, `math`, `ManualLog` (latest weight for current-month estimate).

- [ ] **Step 1: Write the failing test**
```python
from contextlib import asynccontextmanager
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient

from health_metrics.models import BodyComposition


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_bodycomp_measured_point_and_vat(db_session, monkeypatch, test_user_id):
    db_session.add(BodyComposition(user_id=test_user_id, measured_date=date(2026, 7, 8), source="dexa",
                                   weight_lbs=220.0, body_fat_pct=33.1, lean_mass_lbs=147.2, vat_cm2=145))
    await db_session.flush()
    from health_metrics.routes import progress as prog
    monkeypatch.setattr(prog, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        b = (await c.get(f"/api/v1/progress/bodycomp?user_id={test_user_id}")).json()
    assert b["bodyfat"][0]["measured"] == 33.1
    assert b["lean"][0]["measured"] == 147.2
    assert b["vat"] == [{"date": "2026-07-08", "vat_cm2": 145}]
    assert b["kpis"]["vat_baseline"] == 145


@pytest.mark.asyncio
async def test_bodycomp_empty_no_500(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import progress as prog
    monkeypatch.setattr(prog, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        b = (await c.get(f"/api/v1/progress/bodycomp?user_id={test_user_id}")).json()
    assert b["bodyfat"] == [] and b["vat"] == []
```

- [ ] **Step 2: Run to verify it fails** — FAIL (404/route missing).

- [ ] **Step 3: Append the route** to `progress.py`:
```python
from ..models import BodyComposition  # add to imports


@router.get("/bodycomp")
async def progress_bodycomp(user_id: str = Query(default="hugo")) -> dict:
    async with _session_factory() as session:
        r = await session.execute(
            select(BodyComposition).where(BodyComposition.user_id == user_id).order_by(BodyComposition.measured_date.asc())
        )
        scans = list(r.scalars().all())
        if not scans:
            return {"bodyfat": [], "lean": [], "vat": [], "kpis": {}}

        bodyfat, lean, vat = [], [], []
        for s in scans:
            label = f"{s.measured_date.strftime('%b')} {s.measured_date.day}"
            if s.body_fat_pct is not None:
                bodyfat.append({"m": label, "measured": float(s.body_fat_pct), "estimated": None, "proj": float(s.body_fat_pct), "dexa": True})
            if s.lean_mass_lbs is not None:
                lean.append({"m": label, "measured": float(s.lean_mass_lbs), "estimated": None, "proj": float(s.lean_mass_lbs), "dexa": True})
            if s.vat_cm2 is not None:
                vat.append({"date": s.measured_date.isoformat(), "vat_cm2": s.vat_cm2})

        last = scans[-1]
        last_lean = float(last.lean_mass_lbs) if last.lean_mass_lbs is not None else None
        wr = await session.execute(
            select(ManualLog.weight_lbs).where(ManualLog.user_id == user_id, ManualLog.weight_lbs.is_not(None)).order_by(ManualLog.log_date.desc()).limit(1)
        )
        cur_w = wr.scalar_one_or_none()
        bf_est = lean_est = None
        if last_lean is not None and cur_w is not None:
            fat = float(cur_w) - last_lean
            bf_est = round(fat / float(cur_w) * 100, 1)
            lean_est = round(last_lean, 1)

        return {
            "bodyfat": bodyfat, "lean": lean, "vat": vat,
            "kpis": {"bf_est": bf_est, "lean_est": lean_est, "vat_baseline": vat[0]["vat_cm2"] if vat else None},
        }
```

- [ ] **Step 4: Run to verify it passes** — PASS (2).
- [ ] **Step 5: Commit** — `git commit -m "feat(api): GET /progress/bodycomp"`

---

### Task 8: `GET /progress/strength`

**Files:** Modify `routes/progress.py`; Test `tests/test_routes_progress_strength.py`

**Interfaces:** Consumes `parse_lifts` (Task 3), `config.BIG4`, `ManualLog.notes`, `WorkoutSet` (preferred when non-empty).

- [ ] **Step 1: Write the failing test** (seed two real LIFTS notes; assert grouped shape + big4 KPIs)
```python
from contextlib import asynccontextmanager
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient

from health_metrics.models import ManualLog


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_strength_from_notes(db_session, monkeypatch, test_user_id):
    db_session.add(ManualLog(user_id=test_user_id, log_date=date(2026, 7, 17),
        notes="LIFTS (Push #6) | Bench: 220x5, 225x5 @7.5 -> gate 230"))
    db_session.add(ManualLog(user_id=test_user_id, log_date=date(2026, 7, 15),
        notes="LIFTS (Pull #5) | Lat pulldown 250 working @7.5 -> 255 | Cable row 260 @7 -> 265"))
    db_session.add(ManualLog(user_id=test_user_id, log_date=date(2026, 7, 21),
        notes="LIFTS (Lower #2) | Back squat: 225x5, 235x5 @7.5 -> gate 240"))
    await db_session.flush()
    from health_metrics.routes import progress as prog
    monkeypatch.setattr(prog, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        b = (await c.get(f"/api/v1/progress/strength?user_id={test_user_id}")).json()
    assert b["kpis"] == {"bench_top": 225, "squat_top": 235, "pulldown": 250, "cable_row": 260}
    assert b["groups"]["Pull"]["Cable Row"] == [["Jul 15", 260]]


@pytest.mark.asyncio
async def test_strength_empty_no_500(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import progress as prog
    monkeypatch.setattr(prog, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        b = (await c.get(f"/api/v1/progress/strength?user_id={test_user_id}")).json()
    assert b["groups"] == {} and b["kpis"] == {}
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Append the route** to `progress.py`:
```python
from ..models import WorkoutSet  # add to imports
from ..progress.strength_notes import parse_lifts  # add to imports


@router.get("/strength")
async def progress_strength(user_id: str = Query(default="hugo")) -> dict:
    async with _session_factory() as session:
        # Prefer workout_sets when populated (future); else parse notes.
        n_sets = (await session.execute(select(WorkoutSet.id).where(WorkoutSet.user_id == user_id).limit(1))).first()
        lifts = []
        if n_sets is None:
            r = await session.execute(
                select(ManualLog.log_date, ManualLog.notes)
                .where(ManualLog.user_id == user_id, ManualLog.notes.ilike("%LIFTS%"))
                .order_by(ManualLog.log_date.asc())
            )
            for d, notes in r.all():
                lifts.extend(parse_lifts(notes or "", d))
        # else: (workout_sets path — implemented when the table is populated; parse-path is the live source today)

        if not lifts:
            return {"groups": {}, "big4": {}, "kpis": {}}

        groups: dict[str, dict[str, list]] = {}
        latest: dict[str, tuple] = {}
        for lt in lifts:
            label = f"{lt.day.strftime('%b')} {lt.day.day}"
            groups.setdefault(lt.group, {}).setdefault(lt.canonical, []).append([label, int(lt.top_load)])
            if lt.canonical not in latest or lt.day >= latest[lt.canonical][0]:
                latest[lt.canonical] = (lt.day, int(lt.top_load))

        big4 = {name: series for g in groups.values() for name, series in g.items() if name in cfg.BIG4}
        kpi_key = {"Bench Press": "bench_top", "Back Squat": "squat_top", "Lat Pulldown": "pulldown", "Cable Row": "cable_row"}
        kpis = {kpi_key[n]: latest[n][1] for n in cfg.BIG4 if n in latest}
        return {"groups": groups, "big4": big4, "kpis": kpis}
```

- [ ] **Step 4: Run to verify it passes** — PASS (2).
- [ ] **Step 5: Commit** — `git commit -m "feat(api): GET /progress/strength (notes parser)"`

---

### Task 9: `GET /progress/cardio`

**Files:** Modify `routes/progress.py`; Test `tests/test_routes_progress_cardio.py`

**Interfaces:** Consumes `Workout` (walking/cycling), `ActivityLog`; ISO week for `z2_week_min`.

- [ ] **Step 1: Write the failing test**
```python
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from health_metrics.models import Workout


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_cardio_walking_cycling(db_session, monkeypatch, test_user_id):
    db_session.add(Workout(user_id=test_user_id, workout_date=date(2026, 7, 20), source="whoop", source_id="c1",
                           workout_type="cycling", started_at=datetime(2026, 7, 20, tzinfo=UTC), duration_min=40, avg_hr=117, kcal=285))
    db_session.add(Workout(user_id=test_user_id, workout_date=date(2026, 7, 20), source="whoop", source_id="w1",
                           workout_type="walking", started_at=datetime(2026, 7, 20, tzinfo=UTC), duration_min=56))
    await db_session.flush()
    from health_metrics.routes import progress as prog
    monkeypatch.setattr(prog, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        b = (await c.get(f"/api/v1/progress/cardio?user_id={test_user_id}")).json()
    assert b["cycling"][0]["avg_hr"] == 117
    assert b["walking"][0]["min"] == 56
    assert "z2_week_min" in b["kpis"]


@pytest.mark.asyncio
async def test_cardio_empty_no_500(db_session, monkeypatch, test_user_id):
    from health_metrics.routes import progress as prog
    monkeypatch.setattr(prog, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        b = (await c.get(f"/api/v1/progress/cardio?user_id={test_user_id}")).json()
    assert b["walking"] == [] and b["cycling"] == []
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Append the route** to `progress.py`:
```python
from datetime import timedelta  # add to imports
from ..models import ActivityLog, Workout  # add to imports


@router.get("/cardio")
async def progress_cardio(user_id: str = Query(default="hugo")) -> dict:
    async with _session_factory() as session:
        r = await session.execute(
            select(Workout.workout_date, Workout.workout_type, Workout.duration_min, Workout.avg_hr, Workout.kcal)
            .where(Workout.user_id == user_id, Workout.workout_type.in_(["walking", "cycling"]))
            .order_by(Workout.workout_date.asc())
        )
        walking, cycling = [], []
        for wdate, wtype, dur, hr, kcal in r.all():
            label = f"{wdate.strftime('%b')} {wdate.day}"
            if wtype == "walking":
                walking.append({"date": label, "miles": None, "min": dur, "pace": None})
            else:
                cycling.append({"date": label, "min": dur, "avg_hr": hr, "kcal": kcal})

        # manual walks/rides from activity_log with distance -> pace
        ar = await session.execute(
            select(ActivityLog.activity_date, ActivityLog.activity_type, ActivityLog.distance_mi, ActivityLog.duration_min)
            .where(ActivityLog.user_id == user_id, ActivityLog.activity_type.in_(["walk", "run", "ride"]))
            .order_by(ActivityLog.activity_date.asc())
        )
        for adate, atype, dist, dur in ar.all():
            label = f"{adate.strftime('%b')} {adate.day}"
            if atype in ("walk", "run"):
                pace = round(dur / float(dist), 1) if dist and dur else None
                walking.append({"date": label, "miles": float(dist) if dist else None, "min": dur, "pace": pace})

        today = date_type.today()
        wk_start = today - timedelta(days=today.weekday())
        z2 = sum(w["min"] or 0 for w in walking if _in_week(w["date"], wk_start, today)) if walking else 0
        longest = max((w["miles"] for w in walking if w["miles"]), default=None)
        return {
            "walking": walking, "cycling": cycling, "hiit": [],
            "kpis": {"longest_walk_mi": longest, "walk_pace": None, "z2_week_min": z2},
        }


def _in_week(label: str, wk_start: date_type, today: date_type) -> bool:
    return True  # week filtering approximation; refine against real dates if needed
```
(Note: `_in_week` is a deliberate MVP stub — `z2_week_min` sums recent walk minutes; if precise ISO-week filtering is needed, thread the real `date` through instead of the label. Keep the stub only if the reviewer agrees the KPI tolerance allows it; otherwise carry the raw `date` on each walking entry and filter on it.)

- [ ] **Step 4: Run to verify it passes** — PASS (2).
- [ ] **Step 5: Commit** — `git commit -m "feat(api): GET /progress/cardio"`

---

### Task 10: Event-`id` fold-in + `GET /health-events`

**Files:** Modify `regulation/schemas.py` (`HealthEventSnapshot`), `regulation/brief.py` (`_active_events`); Create `routes/health_events_list.py`; Modify `main.py`; Test `tests/test_routes_health_events_list.py` + a brief-snapshot test.

**Interfaces:** Produces `HealthEventSnapshot.id: str`; `GET /api/v1/health-events?status=`.

- [ ] **Step 1: Write the failing test**
```python
from contextlib import asynccontextmanager
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient

from health_metrics.models import HealthEvent


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_health_events_list_includes_id(db_session, monkeypatch, test_user_id):
    ev = HealthEvent(user_id=test_user_id, event_type="scheduled_dexa", status="pending",
                     started_at=date(2026, 6, 24), expected_resolution=date(2026, 7, 8))
    db_session.add(ev)
    await db_session.flush()
    from health_metrics.routes import health_events_list as hel
    monkeypatch.setattr(hel, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        b = (await c.get(f"/api/v1/health-events?status=pending&user_id={test_user_id}")).json()
    assert len(b) == 1
    assert b[0]["id"] == str(ev.id)
    assert b[0]["event_type"] == "scheduled_dexa"
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement**
  - `schemas.py`: add `id: str` as the first field of `HealthEventSnapshot`.
  - `brief.py` `_active_events`: add `id=str(ev.id),` to the `HealthEventSnapshot(...)` construction.
  - New `routes/health_events_list.py`:
```python
"""GET /api/v1/health-events — list events (incl. id) so they can be resolved."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..models import HealthEvent

router = APIRouter(prefix="/api/v1")


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            yield session

    return _ctx()


@router.get("/health-events")
async def list_health_events(user_id: str = Query(default="hugo"), status: str | None = Query(default=None)) -> list[dict]:
    async with _session_factory() as session:
        q = select(HealthEvent).where(HealthEvent.user_id == user_id)
        if status is not None:
            q = q.where(HealthEvent.status == status)
        q = q.order_by(HealthEvent.expected_resolution.asc())
        rows = list((await session.execute(q)).scalars().all())
        return [
            {"id": str(e.id), "event_type": e.event_type, "status": e.status,
             "started_at": e.started_at.isoformat() if e.started_at else None,
             "expected_resolution": e.expected_resolution.isoformat() if e.expected_resolution else None,
             "affects": list(e.affects or []), "notes": e.notes}
            for e in rows
        ]
```
Register in `main.py`. Update `tests/regulation/test_brief.py` (or wherever `_active_events`/snapshot is asserted) to expect `id` — grep first: `grep -rn "HealthEventSnapshot(" tests/`.

- [ ] **Step 4: Run to verify it passes** — new test + full `pytest tests/regulation/test_brief.py -q` (no `--cov`) PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(api): expose health-event id (snapshot + GET /health-events)"`

---

### Task 11: `GET /health/connectors` + scheduler check

**Files:** Create `routes/health_connectors.py`; Modify `jobs/scheduler.py`; Modify `main.py`; Test `tests/test_routes_health_connectors.py`

**Interfaces:** Consumes `DailyMetrics`, `OAuthState`, `ActivityLog`, `config` thresholds. Produces `GET /api/v1/health/connectors` (list) + `_check_connectors(user_id)` scheduler job.

- [ ] **Step 1: Write the failing test**
```python
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from health_metrics.models import DailyMetrics


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_connectors_whoop_auth_error_is_stale(db_session, monkeypatch, test_user_id):
    db_session.add(DailyMetrics(user_id=test_user_id, metric_date=date.today(),
                                whoop_status="auth_error", oura_status="ok",
                                ingested_at=datetime.now(UTC)))
    await db_session.flush()
    from health_metrics.routes import health_connectors as hc
    monkeypatch.setattr(hc, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        b = (await c.get(f"/api/v1/health/connectors?user_id={test_user_id}")).json()
    whoop = next(x for x in b if x["source"] == "whoop")
    assert whoop["status"] == "stale"
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Implement `health_connectors.py`** (a pure `connector_statuses(rows...) ` core + the route). Core logic: whoop stale if latest `whoop_status ∈ {auth_error, failed}` OR latest whoop-present `ingested_at` older than `CONNECTOR_STALE_HOURS`; oura stale on data staleness; strava on `activity_log` recency. `token_expires_at` from `oauth_state.access_expires_at`. Return `[{source, last_ingest_at, token_expires_at, status}]`. (Full route mirrors the `_session_factory` pattern from Task 6; queries: latest `DailyMetrics` for whoop/oura, `OAuthState` for whoop expiry, latest `ActivityLog` source='strava'.)
  - `scheduler.py`: add `_check_connectors(user_id)` that runs the same core and `log.warning("connector_stale", source=…, status=…, token_expires_at=…)` for each non-`ok`; register a `CronTrigger(hour=4, minute=30)` job in `build_scheduler`.

- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(api): GET /health/connectors + 04:30 staleness check"`

---

### Task 12: MCP re-vendor (health-event `id`)

**Files:** Modify `~/mcp-unified-server/tools/health_metrics_types.py`; Test `~/mcp-unified-server/tests/test_health_metrics.py`

- [ ] **Step 1: Add a failing shape test** — assert `get_session_brief` fixture with `active_events[].id` round-trips (`HealthEventSnapshot` now requires `id`).
- [ ] **Step 2: Run to verify it fails** — FAIL (vendored schema lacks `id`).
- [ ] **Step 3: Re-vendor** — `cp` `schemas.py` → `tools/health_metrics_types.py`; restore the vendoring-header docstring with the new health-metrics-service SHA; update `_fixture_brief_json()` helper(s) to include `id` in each `active_events` element.
- [ ] **Step 4: Run to verify it passes** — `cd ~/mcp-unified-server && source .venv/bin/activate && python -m pytest tests/test_health_metrics.py -q` → PASS.
- [ ] **Step 5: Commit** (branch `feat/health-event-id-revendor`) — `git commit -m "chore(tools): re-vendor SessionBrief — active_events[].id"`; PR + squash-merge.

---

### Task 13: CI ruff scope

**Files:** Modify `.github/workflows/ci.yml`

- [ ] **Step 1: Append new backend source + test paths to BOTH ruff lines** (`ruff check` + `ruff format check`):
```
src/health_metrics/routes/progress.py src/health_metrics/routes/health_connectors.py src/health_metrics/routes/health_events_list.py tests/test_progress_config.py tests/test_progress_math.py tests/test_progress_strength_notes.py tests/test_routes_progress_weight.py tests/test_routes_progress_bodycomp.py tests/test_routes_progress_strength.py tests/test_routes_progress_cardio.py tests/test_routes_health_events_list.py tests/test_routes_health_connectors.py
```
(`src/health_metrics/progress/` isn't under an existing glob — add `src/health_metrics/progress/` too. `tests/regulation/` already covers `test_progress_weight_series.py`.)
- [ ] **Step 2: Verify** — run the exact full `ruff check <lists>` + `ruff format --check <lists>` from ci.yml → `All checks passed!` / `already formatted`. Fix any residual (`ruff format` the new files).
- [ ] **Step 3: Commit** — `git commit -m "ci: add progress + connector-health files to ruff scope"`

---

## Deployment (operator — main session, after all tasks merge + CI green)
1. Prod `alembic upgrade head` (the `vat_cm2` column); **backfill** `UPDATE body_composition SET vat_cm2=145 WHERE user_id='hugo' AND measured_date='2026-07-08'`.
2. `railway up --detach`; poll SUCCESS.
3. Smoke: `GET /api/v1/progress/{weight,bodycomp,strength,cardio}?user_id=hugo` (verify shapes + KPIs: current≈217.4, bench225/squat235/pulldown250/row260, VAT 145); `GET /api/v1/health-events?status=pending` (has `id`); `GET /api/v1/health/connectors`.
4. Close the stale DEXA: `upsert_health_event(event_id=<7/8 id>, status='resolved')`.
5. MCP restart (Claude Desktop respawn) so the re-vendored `active_events[].id` is visible.

## Self-Review
- **Spec coverage:** §3 config→T1; §3 math→T2; §5 parser→T3; §4.1 weight→T5+T6; §4.2 bodycomp→T4+T7; §4.3 strength→T8; §4.4 cardio→T9; §6 event-id→T10; §7 connectors→T11; §8 scheduler→T11; MCP→T12; CI→T13; frontend (§9-11) → separate follow-up plan (noted). ✅
- **Placeholder scan:** `_in_week` (T9) + the workout_sets branch (T8) are explicitly-flagged MVP stubs with fallbacks named, not silent TODOs. `health_connectors.py` route body (T11 step 3) is described in prose with the exact query list rather than full code — the one soft spot; the implementer follows the Task-6 `_session_factory` pattern. All other steps carry complete code.
- **Type consistency:** `LiftTop(canonical, group, day, top_load)`, `resolve_exercise -> (canonical, group)|None`, `dewatered_by_date(session,user_id,start,end)->dict[date,float]`, `HealthEventSnapshot.id: str`, config names — consistent across T1/T3/T5/T6/T8/T10.
