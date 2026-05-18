# Health Metrics Railway Deploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy `health-metrics-service` + `health-metrics-dashboard` to Railway with a managed Postgres, gated by Cloudflare Access at `health.ironforge.ai`, with the daily Whoop+Oura ingest running via APScheduler in-process.

**Architecture:** Two Railway services (backend + dashboard) + one managed Postgres in a single Railway project. CF Access protects both public subdomains; dashboard SSR talks to backend over Railway's internal IPv6 network to avoid CF round-trips. APScheduler runs inside the backend FastAPI process (single worker) for daily ingest at 06:00 America/New_York.

**Tech Stack:** Railway (Procfile-style deploy), Postgres 16, Cloudflare Access (Google IdP), FastAPI + uvicorn + APScheduler, Next.js 16, alembic, pg_dump/psql for the one-shot data migration.

**Spec reference:** `docs/superpowers/specs/2026-05-18-railway-deploy-design.md` (in the `health-metrics-service` repo).

**State at plan start:**
- `growthink1/health-metrics-service` main HEAD `56147f9` (just shipped Whoop v2 migration + this spec)
- `growthink1/health-metrics-dashboard` main HEAD `e88c4f4`, tag `v0.0.1-localhost-only`
- Local Postgres on `localhost:5433` has 30 days of `daily_metrics`, 1+ `oauth_state` row with rotating Whoop refresh token, 0 manual_log rows, 0 narration_cache rows (will regenerate)
- Railway CLI installed; `railway login` NOT yet run interactively
- `ironforge.ai` is on Cloudflare (Hugo confirmed)

---

## File structure

**`health-metrics-service` additions:**
```
health-metrics-service/
├── Procfile                                       # NEW — Railway start command
├── src/health_metrics/
│   ├── routes/
│   │   └── health.py                              # NEW — GET /health
│   ├── jobs/
│   │   └── scheduler.py                           # NEW — APScheduler wiring
│   └── main.py                                    # MODIFY — mount /health, start/stop scheduler
└── tests/
    ├── test_health.py                             # NEW — /health route tests
    └── test_scheduler.py                          # NEW — scheduler registers job
```

**`health-metrics-dashboard` change:**
```
health-metrics-dashboard/
├── lib/api.ts                                     # MODIFY — dual-URL pattern
├── .env.local.example                             # MODIFY — add API_BASE_URL_INTERNAL
└── README.md                                      # MODIFY — prod URL note
```

Each file has one clear responsibility:
- `health.py` exposes one route returning 200 + DB liveness, nothing else.
- `scheduler.py` owns the APScheduler lifecycle; nothing else imports APScheduler.
- `main.py` only wires the new route + scheduler lifecycle hooks; the route and the scheduler don't know about each other.

---

## Task 1: Backend `GET /health` endpoint

**Files:**
- Create: `src/health_metrics/routes/health.py`
- Create: `tests/test_health.py`
- Modify: `src/health_metrics/main.py` (mount router)

- [ ] **Step 1.1: Write the failing test**

Create `tests/test_health.py`:

```python
"""Health endpoint tests — used by Railway for restart decisions."""

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


@pytest.mark.asyncio
async def test_health_returns_200_when_db_up(db_session, monkeypatch):
    from health_metrics.routes import health as health_route

    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(health_route, "_session_factory", lambda: _ctx())

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}


@pytest.mark.asyncio
async def test_health_returns_503_when_db_down(monkeypatch):
    from health_metrics.routes import health as health_route

    @asynccontextmanager
    async def _ctx():
        # Yield a session that raises on execute()
        class _BrokenSession:
            async def execute(self, *args, **kwargs):
                raise OperationalError("conn", None, Exception("connection refused"))
        yield _BrokenSession()

    monkeypatch.setattr(health_route, "_session_factory", lambda: _ctx())

    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "error"
    assert body["db"] == "down"
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
cd ~/code/health-metrics-service && source .venv/bin/activate
python3 -m pytest tests/test_health.py -xvs 2>&1 | tail -10
```
Expected: ModuleNotFoundError on `health_metrics.routes.health` (route doesn't exist yet).

- [ ] **Step 1.3: Implement the route**

Create `src/health_metrics/routes/health.py`:

```python
"""GET /health — Railway/CF probe. 200 = service + DB up, 503 = DB unreachable."""

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..db import AsyncSessionLocal as _session_factory

log = structlog.get_logger()
router = APIRouter()


@router.get("/health")
async def health() -> JSONResponse:
    try:
        async with _session_factory() as session:
            await session.execute(text("SELECT 1"))
        return JSONResponse({"status": "ok", "db": "ok"}, status_code=200)
    except Exception as e:
        log.warning("health_db_check_failed", error=str(e))
        return JSONResponse({"status": "error", "db": "down"}, status_code=503)
```

- [ ] **Step 1.4: Mount the router in `main.py`**

Open `src/health_metrics/main.py`. Find the existing `app.include_router(...)` lines and add:

```python
from .routes.health import router as health_router
# ...
app.include_router(health_router)
```

Add the import at the top with the other route imports; add the `include_router` call next to the others. Do not remove existing routers.

- [ ] **Step 1.5: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_health.py -xvs 2>&1 | tail -10
```
Expected: 2 passed.

Then full sweep:
```bash
python3 -m pytest -q 2>&1 | tail -5
```
Expected: 42 passed (40 existing + 2 new).

- [ ] **Step 1.6: Commit**

```bash
git add src/health_metrics/routes/health.py tests/test_health.py src/health_metrics/main.py
git commit -m "feat: GET /health endpoint for Railway probes"
git push
```

---

## Task 2: Backend Procfile

**Files:**
- Create: `Procfile`

- [ ] **Step 2.1: Create the Procfile**

Create `Procfile` at the repo root (NOT inside `src/`):

```
web: uvicorn src.health_metrics.main:app --host 0.0.0.0 --port $PORT --workers 1
```

Single line, single trailing newline. `$PORT` is injected by Railway at runtime. `--workers 1` is intentional — APScheduler runs in-process and we want exactly one scheduler.

- [ ] **Step 2.2: Verify uvicorn accepts the command locally**

```bash
cd ~/code/health-metrics-service && source .venv/bin/activate
PORT=8765 uvicorn src.health_metrics.main:app --host 0.0.0.0 --port $PORT --workers 1 > /tmp/proc_smoke.log 2>&1 &
UVI_PID=$!
sleep 4
curl -sf http://localhost:8765/health -o /dev/null && echo "PROCFILE CMD OK" || echo "FAIL"
kill $UVI_PID 2>/dev/null
```
Expected: `PROCFILE CMD OK`.

- [ ] **Step 2.3: Commit**

```bash
git add Procfile
git commit -m "chore: add Procfile for Railway deploy"
git push
```

---

## Task 3: APScheduler daily ingest

**Files:**
- Create: `src/health_metrics/jobs/scheduler.py`
- Create: `tests/test_scheduler.py`
- Modify: `src/health_metrics/main.py` (start/stop scheduler in lifespan hooks)
- Modify: `pyproject.toml` (add `apscheduler` dep)

- [ ] **Step 3.1: Add the apscheduler dependency**

Open `pyproject.toml`. Find the `dependencies = [...]` array (top-level under `[project]`). Add `"apscheduler>=3.10.0",` to the list, keeping alphabetical order if existing entries are alphabetized; otherwise add at the end of the list.

Then:
```bash
cd ~/code/health-metrics-service && source .venv/bin/activate
pip install -e .
python3 -c "import apscheduler; print(apscheduler.__version__)"
```
Expected: a version >= 3.10.0 printed.

- [ ] **Step 3.2: Write the failing test**

Create `tests/test_scheduler.py`:

```python
"""Scheduler registers the daily-ingest cron job at the configured time."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


def test_build_scheduler_registers_daily_ingest_job():
    from health_metrics.jobs.scheduler import build_scheduler

    sched = build_scheduler(user_id="hugo")
    try:
        jobs = sched.get_jobs()
        assert len(jobs) == 1, f"expected 1 job, got {len(jobs)}"
        job = jobs[0]
        assert job.id == "daily_ingest"
        assert isinstance(job.trigger, CronTrigger)
        # CronTrigger.fields is a list — find the hour field
        hour_field = next(f for f in job.trigger.fields if f.name == "hour")
        assert str(hour_field) == "6", f"expected hour=6, got {hour_field}"
        # Timezone is America/New_York
        assert str(job.trigger.timezone) == "America/New_York"
    finally:
        sched.shutdown(wait=False)


def test_build_scheduler_uses_correct_user_id():
    from health_metrics.jobs.scheduler import build_scheduler

    sched = build_scheduler(user_id="test_user_xyz")
    try:
        jobs = sched.get_jobs()
        # The job kwargs should bind user_id
        assert jobs[0].kwargs.get("user_id") == "test_user_xyz"
    finally:
        sched.shutdown(wait=False)
```

- [ ] **Step 3.3: Run to verify failure**

```bash
python3 -m pytest tests/test_scheduler.py -xvs 2>&1 | tail -10
```
Expected: ModuleNotFoundError on `health_metrics.jobs.scheduler`.

- [ ] **Step 3.4: Implement the scheduler module**

Create `src/health_metrics/jobs/scheduler.py`:

```python
"""APScheduler integration — runs the daily Whoop+Oura ingest at 06:00 ET.

Idempotent: running multiple times in a day just upserts the same daily_metrics row.
Runs in-process inside the FastAPI worker — only safe with --workers 1.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..db import AsyncSessionLocal
from .daily_ingest import run_daily_ingest

log = structlog.get_logger()

_SCHEDULER_TZ = "America/New_York"
_SCHEDULER_HOUR = 6
_SCHEDULER_MINUTE = 0


async def _run_ingest_for_yesterday(user_id: str) -> None:
    """Callback the scheduler fires every day. Ingests yesterday in ET."""
    tz = ZoneInfo(_SCHEDULER_TZ)
    yesterday = (datetime.now(tz) - timedelta(days=1)).date()
    log.info("scheduler_tick", target_day=yesterday.isoformat(), user_id=user_id)
    async with AsyncSessionLocal() as session:
        try:
            result = await run_daily_ingest(day=yesterday, user_id=user_id, session=session)
            log.info("scheduler_tick_complete", **result)
        except Exception as e:
            log.error("scheduler_tick_failed", error=str(e), day=yesterday.isoformat())


def build_scheduler(user_id: str) -> AsyncIOScheduler:
    """Build (do not start) an AsyncIOScheduler with the daily-ingest job."""
    sched = AsyncIOScheduler(timezone=_SCHEDULER_TZ)
    sched.add_job(
        _run_ingest_for_yesterday,
        trigger=CronTrigger(hour=_SCHEDULER_HOUR, minute=_SCHEDULER_MINUTE, timezone=_SCHEDULER_TZ),
        kwargs={"user_id": user_id},
        id="daily_ingest",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,  # 1h grace window for tick recovery after restart
    )
    return sched
```

- [ ] **Step 3.5: Wire start/stop into `main.py`**

Open `src/health_metrics/main.py`. Find the FastAPI `app = FastAPI(...)` instantiation. Find any existing startup/shutdown hooks or `lifespan` context.

If `main.py` uses `@app.on_event("startup")` style, add:

```python
from .config import get_settings
from .jobs.scheduler import build_scheduler

_scheduler = None

@app.on_event("startup")
async def _start_scheduler():
    global _scheduler
    settings = get_settings()
    _scheduler = build_scheduler(settings.user_id)
    _scheduler.start()

@app.on_event("shutdown")
async def _stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
```

If `main.py` uses the newer `lifespan=` pattern, add the start/stop inside the existing `lifespan` async context manager — don't introduce a second one. Before editing, run `grep -n 'on_event\|lifespan' src/health_metrics/main.py` to see which pattern is in use, then match it.

- [ ] **Step 3.6: Run all tests**

```bash
python3 -m pytest -q 2>&1 | tail -5
```
Expected: 44 passed (42 from Task 1 + 2 new scheduler tests).

- [ ] **Step 3.7: Smoke that the scheduler boots without crashing the app**

```bash
PORT=8766 uvicorn src.health_metrics.main:app --host 0.0.0.0 --port $PORT --workers 1 > /tmp/sched_smoke.log 2>&1 &
UVI_PID=$!
sleep 5
curl -sf http://localhost:8766/health -o /dev/null && echo "HEALTHY" || echo "FAIL"
grep -E 'scheduler|apscheduler' /tmp/sched_smoke.log | head -3
kill $UVI_PID 2>/dev/null
```
Expected: `HEALTHY` + log lines mentioning the scheduler started. No tracebacks.

- [ ] **Step 3.8: Commit**

```bash
git add pyproject.toml src/health_metrics/jobs/scheduler.py tests/test_scheduler.py src/health_metrics/main.py
git commit -m "feat: APScheduler daily ingest at 06:00 ET"
git push
```

---

## Task 4: Dashboard dual-URL refactor

**Files:**
- Modify: `lib/api.ts` (5 lines)
- Modify: `.env.local.example`
- Modify: `tests/api.test.ts` (add 1 test)

- [ ] **Step 4.1: Update `lib/api.ts`**

Open `/Users/ironforgeai/code/health-metrics-dashboard/lib/api.ts`. Find this line near the top:

```ts
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
```

Replace it with:

```ts
const isServer = typeof window === "undefined";
const API_BASE = isServer
  ? (process.env.API_BASE_URL_INTERNAL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000")
  : (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000");
```

That's the only line change in this file. The `_get` helper and all wrappers stay the same; they read `API_BASE` at call time.

- [ ] **Step 4.2: Update `.env.local.example`**

Open `/Users/ironforgeai/code/health-metrics-dashboard/.env.local.example`. Add a new section under the existing one:

```bash
# Backend base URL — what the BROWSER fetches from.
# In dev: http://localhost:8000
# In prod (Railway+CF): https://api.health.ironforge.ai
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

# Backend base URL — what server-side renders (SSR) and route handlers fetch from.
# In dev: same as NEXT_PUBLIC_API_BASE_URL (single local backend).
# In prod (Railway): http://backend.railway.internal:8000 — uses Railway's private network,
# bypasses Cloudflare Access entirely.
API_BASE_URL_INTERNAL=http://localhost:8000
```

Replace the existing single-var section with this two-var version. Also update `.env.local` (gitignored) to match.

- [ ] **Step 4.3: Add a Vitest test for the dual-URL behavior**

Open `/Users/ironforgeai/code/health-metrics-dashboard/tests/api.test.ts`. Add this test inside the existing `describe("api client", () => { ... })` block, after the last existing test:

```ts
it("uses NEXT_PUBLIC_API_BASE_URL when called from the browser (window defined)", async () => {
  // Simulate a browser environment by ensuring window is defined
  // Vitest's jsdom environment already provides window, so just set both env vars and verify the browser one wins.
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://browser.example";
  process.env.API_BASE_URL_INTERNAL = "https://internal.example";

  const mockFetch = vi.fn(async () =>
    new Response(JSON.stringify({ metric_date: "2026-05-13" }), { status: 200 }),
  );
  globalThis.fetch = mockFetch as typeof fetch;

  // Re-import after env mutation, since API_BASE is module-scope
  vi.resetModules();
  const { fetchDashboardToday } = await import("@/lib/api");
  await fetchDashboardToday("hugo");

  expect(mockFetch).toHaveBeenCalledWith(
    expect.stringContaining("https://browser.example/api/dashboard/today"),
    expect.anything(),
  );
});
```

- [ ] **Step 4.4: Run dashboard tests**

```bash
cd ~/code/health-metrics-dashboard
npm run test 2>&1 | tail -8
```
Expected: 5 tests passed (4 existing + 1 new).

- [ ] **Step 4.5: Smoke that dev still works against localhost backend**

Backend needs to be running on port 8000 first. Then:

```bash
cd ~/code/health-metrics-dashboard
pkill -f 'next-server\|next dev' 2>/dev/null
sleep 1
nohup npm run dev > /tmp/dash_t4_smoke.log 2>&1 &
disown
sleep 6
curl -s 'http://localhost:3000/' | grep -E 'Recommend|HRV' >/dev/null && echo "DEV SSR STILL WORKS" || echo "FAIL"
pkill -f 'next-server\|next dev' 2>/dev/null
```
Expected: `DEV SSR STILL WORKS`.

- [ ] **Step 4.6: Run the full build + e2e to catch any type/lint regressions**

```bash
cd ~/code/health-metrics-dashboard
npm run build 2>&1 | tail -8
```
Expected: TypeScript strict + ESLint clean, three routes built.

- [ ] **Step 4.7: Commit**

```bash
git add lib/api.ts tests/api.test.ts .env.local.example
git commit -m "refactor: dual-URL pattern in lib/api.ts for SSR vs browser fetches"
git push
```

---

## Task 5: Railway project provisioning

**Files:** None in either repo. All work happens via the Railway CLI.

**Prerequisite:** `railway login` must be completed interactively by Hugo before this task starts. If `railway whoami` returns an error, ask Hugo to run `railway login` (opens a browser).

- [ ] **Step 5.1: Verify Railway auth**

```bash
railway whoami
```
Expected: Hugo's email. If not, stop and ask Hugo to `railway login`.

- [ ] **Step 5.2: Create the Railway project**

```bash
railway init
# When prompted, name the project: health-metrics
# Choose "Empty project" template
```
Expected: a `.railway` directory gets created in the cwd. Note: Hugo's machine uses `~/code/health-metrics-service` and `~/code/health-metrics-dashboard` — run this command from `~/code/health-metrics-service` (it will create `.railway/` there; ignore via `.gitignore` if not already).

- [ ] **Step 5.3: Add Postgres**

```bash
railway add --database postgres
```
Expected: a Postgres service named `Postgres` is added to the project. The `DATABASE_URL` env var is auto-set for sibling services.

Verify with:
```bash
railway variables --service Postgres | head -5
```
Expected: lines including `DATABASE_URL`, `PGHOST`, `PGUSER`, etc.

- [ ] **Step 5.4: Add the backend service from the GitHub repo**

```bash
railway service create backend
# Or via web UI: connect the growthink1/health-metrics-service GitHub repo, name the service "backend"
```

If Railway requires it via the web UI: log into railway.app, open the `health-metrics` project, click "+ New" → "GitHub Repo" → `growthink1/health-metrics-service` → name it `backend`. Railway auto-detects the Procfile.

- [ ] **Step 5.5: Add the dashboard service from the GitHub repo**

Same pattern from `~/code/health-metrics-dashboard`:

```bash
cd ~/code/health-metrics-dashboard
railway link --project health-metrics
railway service create dashboard
```

Or via web UI: in the same project, "+ New" → "GitHub Repo" → `growthink1/health-metrics-dashboard` → name `dashboard`. Railway auto-detects Next.js.

- [ ] **Step 5.6: Verify project layout**

```bash
railway status
```
Expected output should show three services: `Postgres`, `backend`, `dashboard`.

- [ ] **Step 5.7: No commit needed — this task is pure ops.**

---

## Task 6: Set env vars on both services

**Prerequisite:** Hugo has rotated `ANTHROPIC_API_KEY` and provided the new value. If not, ask Hugo for the new key before starting.

- [ ] **Step 6.1: Set backend env vars**

```bash
cd ~/code/health-metrics-service
railway service backend
railway variables set DATABASE_URL='${{Postgres.DATABASE_URL}}' \
  USER_ID=hugo \
  TIMEZONE=America/New_York \
  LOG_LEVEL=INFO \
  WHOOP_CLIENT_ID=<value-from-local-.env> \
  WHOOP_CLIENT_SECRET=<value-from-local-.env> \
  WHOOP_REDIRECT_URI=https://api.health.ironforge.ai/whoop/callback \
  OURA_PERSONAL_TOKEN=<value-from-local-.env> \
  ANTHROPIC_API_KEY=<rotated-key> \
  NARRATION_MODEL=claude-haiku-4-5-20251001 \
  CORS_ALLOWED_ORIGINS='["https://health.ironforge.ai"]'
```

`${{Postgres.DATABASE_URL}}` is Railway's variable reference syntax; it expands at deploy time to Postgres's internal connection string. Quote it carefully — single quotes around the whole value or the shell will try to interpret `$`.

To read the local values without printing the secrets to chat:

```bash
grep '^WHOOP_CLIENT_ID=' ~/code/health-metrics-service/.env | cut -d= -f2-
grep '^WHOOP_CLIENT_SECRET=' ~/code/health-metrics-service/.env | cut -d= -f2-
grep '^OURA_PERSONAL_TOKEN=' ~/code/health-metrics-service/.env | cut -d= -f2-
```

Note: do NOT set `WHOOP_REFRESH_TOKEN` as an env var. The rotating refresh token lives in the `oauth_state` Postgres row that we migrate in Task 7. Setting an env-var fallback would just go stale.

- [ ] **Step 6.2: Set dashboard env vars**

After Task 5.4 you should have the backend's Railway-assigned public URL. Get it:

```bash
cd ~/code/health-metrics-service
railway service backend
railway domain  # prints the auto-assigned *.up.railway.app domain
```

Then:

```bash
cd ~/code/health-metrics-dashboard
railway service dashboard
railway variables set NEXT_PUBLIC_API_BASE_URL=https://api.health.ironforge.ai \
  API_BASE_URL_INTERNAL=http://backend.railway.internal:8000 \
  NODE_ENV=production
```

- [ ] **Step 6.3: Verify env vars are set on both services**

```bash
cd ~/code/health-metrics-service && railway service backend && railway variables | grep -E '^(DATABASE_URL|WHOOP_|OURA_|ANTHROPIC_|NARRATION_|CORS_|USER_ID|TIMEZONE|LOG_LEVEL)='
echo "---"
cd ~/code/health-metrics-dashboard && railway service dashboard && railway variables | grep -E '^(NEXT_PUBLIC_|API_BASE_|NODE_ENV)='
```
Expected: backend shows 9 env vars (excluding auto-set Railway internals); dashboard shows 3.

- [ ] **Step 6.4: No commit — env vars are Railway-side only.**

---

## Task 7: Database migration

**Prerequisite:** Postgres service is up (Task 5.3 ✓) and backend env vars are set (Task 6.1 ✓ — gives us alembic the right `DATABASE_URL`).

- [ ] **Step 7.1: Get the Railway Postgres public connection string**

We need an externally-reachable connection string for the one-shot `psql` import. The internal `${{Postgres.DATABASE_URL}}` only works from inside Railway services.

```bash
cd ~/code/health-metrics-service
railway service Postgres
railway variables | grep -E '^DATABASE_PUBLIC_URL|^DATABASE_URL='
```

If only the internal `DATABASE_URL` is shown, expose the public one:
```bash
railway domain  # may print or generate a TCP proxy URL for Postgres
```

Save the public URL into a temp shell var for the next steps (do NOT echo it to chat):
```bash
export RAILWAY_PG_URL='postgresql://...'   # use the value from railway variables
```

- [ ] **Step 7.2: Run alembic migrations against Railway Postgres**

```bash
cd ~/code/health-metrics-service && source .venv/bin/activate
# Use the asyncpg-compatible URL form alembic expects
RAILWAY_PG_URL_SYNC=$(echo "$RAILWAY_PG_URL" | sed 's|postgresql+asyncpg://|postgresql://|' | sed 's|^postgresql://|postgresql+psycopg2://|')
DATABASE_URL="$RAILWAY_PG_URL_SYNC" alembic upgrade head 2>&1 | tail -5
```

If alembic is configured for async (via env.py), use the asyncpg URL directly:
```bash
DATABASE_URL="$RAILWAY_PG_URL" alembic upgrade head 2>&1 | tail -5
```

Run `grep -n 'asyncpg\|psycopg2\|engine_from_config' alembic/env.py` first if uncertain. Expected output: alembic log lines ending in "Will assume non-transactional DDL" or similar, then no errors.

- [ ] **Step 7.3: Dump local data**

```bash
docker exec hms-postgres pg_dump -U hms -d health_metrics --no-owner --no-acl --data-only > /tmp/hms_data.sql
wc -l /tmp/hms_data.sql
head -20 /tmp/hms_data.sql
```
Expected: a multi-thousand-line SQL file starting with `COPY ... FROM stdin;` blocks.

`--data-only` means we don't try to recreate tables that alembic just made.

- [ ] **Step 7.4: Import data to Railway**

```bash
psql "$RAILWAY_PG_URL" < /tmp/hms_data.sql 2>&1 | tail -10
```
Expected: a series of `COPY <n>` lines and `SETVAL` lines (sequence resets). No `ERROR:` lines.

- [ ] **Step 7.5: Verify the data landed**

```bash
psql "$RAILWAY_PG_URL" -c "
SELECT 'daily_metrics' AS t, COUNT(*) FROM daily_metrics
UNION ALL SELECT 'workouts', COUNT(*) FROM workouts
UNION ALL SELECT 'oauth_state', COUNT(*) FROM oauth_state
ORDER BY t;
"
```
Expected: daily_metrics ≥ 30, workouts ≥ 3 (the cycling sessions), oauth_state = 1 (Whoop row).

- [ ] **Step 7.6: Delete the local dump**

```bash
rm /tmp/hms_data.sql
```
Cleanup — the dump contains the rotating Whoop refresh token.

- [ ] **Step 7.7: No commit — Railway DB state is operational.**

---

## Task 8: Cloudflare DNS + Access policies

**Files:** None. Cloudflare dashboard.

- [ ] **Step 8.1: Get the Railway service public URLs**

```bash
cd ~/code/health-metrics-service && railway service backend && railway domain
cd ~/code/health-metrics-dashboard && railway service dashboard && railway domain
```
Note both URLs. They'll look like `backend-production-abcd.up.railway.app` and similar.

- [ ] **Step 8.2: Add CNAMEs in Cloudflare**

In the CF dashboard for `ironforge.ai`:

1. DNS → Add record:
   - Type: `CNAME`
   - Name: `health`
   - Target: `<dashboard-railway-url>` (from step 8.1)
   - Proxy status: **Proxied** (orange cloud ON)
   - TTL: Auto

2. DNS → Add record:
   - Type: `CNAME`
   - Name: `api.health` (CF allows multi-level)
   - Target: `<backend-railway-url>`
   - Proxy status: **Proxied**
   - TTL: Auto

- [ ] **Step 8.3: Verify DNS resolves**

```bash
dig +short health.ironforge.ai
dig +short api.health.ironforge.ai
```
Expected: both return Cloudflare IPs (orange-cloud means CF acts as origin). Propagation is usually instant for CF-proxied records.

- [ ] **Step 8.4: Add custom domains to the Railway services**

```bash
cd ~/code/health-metrics-service && railway service backend && railway domain add api.health.ironforge.ai
cd ~/code/health-metrics-dashboard && railway service dashboard && railway domain add health.ironforge.ai
```

Railway will configure its edge to terminate TLS for these hostnames. Verify each command prints a "domain added" confirmation.

- [ ] **Step 8.5: Create Cloudflare Access applications**

In the CF dashboard: Zero Trust → Access → Applications → Add an application → Self-hosted.

**Application 1:**
- Application name: `health-metrics dashboard`
- Session duration: 30 days
- Application domain: `health.ironforge.ai`
- Identity providers: Google
- Click Next.

Policy:
- Policy name: `Hugo only`
- Action: Allow
- Configure rules → Include → Selector: `Emails` → Value: `hdelgad2@alumni.nd.edu`
- Save.

**Application 2:**
Same as above but `api.health.ironforge.ai`. Identical policy.

For the cookie to be shared across both subdomains, in each app: Advanced Settings → set "Custom domain" cookie to `.ironforge.ai` (without a leading subdomain).

- [ ] **Step 8.6: Smoke the CF gate**

In an incognito browser, open `https://health.ironforge.ai`. Expected:
1. CF Access redirects to a Google login page.
2. Sign in with Hugo's Google account.
3. CF Access lets the request through.
4. Page loads — should show the dashboard.

If the dashboard 500s because backend isn't healthy yet, that's OK — Task 9 will verify the full end-to-end path.

- [ ] **Step 8.7: No commit — CF state is dashboard-side.**

---

## Task 9: End-to-end smoke

**Prerequisite:** Tasks 1-8 complete. Both services deployed, DNS proxied, CF Access live.

- [ ] **Step 9.1: Verify backend health from outside CF Access**

This step uses a CF service token to bypass the user-auth requirement — only for ops verification.

In CF Access dashboard → Service Auth → Create a service token named `ops-smoke`. Save the Client ID + Secret. Add a service-token rule to both Access apps that allows the `ops-smoke` token through.

Then:
```bash
curl -H "CF-Access-Client-Id: <client-id>" \
     -H "CF-Access-Client-Secret: <client-secret>" \
     https://api.health.ironforge.ai/health
```
Expected: `{"status":"ok","db":"ok"}` HTTP 200.

(If you'd rather skip the service token: just open `https://api.health.ironforge.ai/health` in a browser after logging into the dashboard — the CF cookie carries through.)

- [ ] **Step 9.2: Browser-load the dashboard end-to-end**

1. Open `https://health.ironforge.ai/` in an incognito browser
2. Sign in via CF Access (Google)
3. Confirm the page renders: TodayStrip with real values (recovery, HRV, RHR, strain), NarrationLine has a real Claude-generated sentence, 6 sparkline tiles, WindowSelector

If something doesn't render: check Railway logs (`railway logs --service dashboard`, `railway logs --service backend`).

- [ ] **Step 9.3: Test the LogPanel POST**

In the same browser session, type values into the LogPanel inputs (energy=7, mood=7, hunger=6) and click Save. Expected:
- No console errors
- The "Save" button briefly says "Saving…"
- The LogPanel re-renders without the subjective_missing token (or hides entirely if you also entered weight)

Verify via Postgres:
```bash
psql "$RAILWAY_PG_URL" -c "SELECT user_id, log_date, subjective_energy, subjective_mood, subjective_hunger FROM manual_log WHERE user_id='hugo' ORDER BY log_date DESC LIMIT 3;"
```
Expected: one row for today (in ET) with the values you just submitted.

- [ ] **Step 9.4: Drill-down route works**

Click any sparkline tile (e.g., HRV). Expected: `/metric/hrv` loads with a Recharts line chart + day-by-day table. Confirm 30 days of points are visible when `?days=30` is set.

- [ ] **Step 9.5: Workouts route works**

Click "Workouts" in the header. Expected: rendered table with the 3+ cycling sessions from the backfill.

- [ ] **Step 9.6: Note the next-day scheduler verification**

The scheduler can't be smoked synchronously — it fires at 06:00 ET tomorrow. After ~24h:

```bash
railway logs --service backend | grep -E 'scheduler_tick|daily_ingest_complete' | tail -5
```
Expected: a `scheduler_tick` event around 06:00 ET timestamp, followed by a `daily_ingest_complete` event.

Add a reminder to followups memory note to check this the first morning after deploy.

- [ ] **Step 9.7: Delete the service token if you created one**

CF Access → Service Auth → revoke the `ops-smoke` token. Optional but cleaner.

---

## Task 10: Tag releases + README updates

**Files:**
- Modify: `~/code/health-metrics-dashboard/README.md` (deploy section)
- Modify: `~/code/health-metrics-service/README.md` (if exists; else create with deploy section)

- [ ] **Step 10.1: Update dashboard README**

Open `~/code/health-metrics-dashboard/README.md`. Find the `## Deploy` section. Replace its contents (the placeholder added in Plan 2 T11) with:

```markdown
## Deploy

Lives on Railway under the `health-metrics` project, behind Cloudflare Access at `https://health.ironforge.ai`. Only `hdelgad2@alumni.nd.edu` can sign in (CF Access policy).

- **Dashboard service:** Auto-deploys from `growthink1/health-metrics-dashboard` `main`.
- **Backend service:** Auto-deploys from `growthink1/health-metrics-service` `main`. Talks to managed Postgres; runs APScheduler daily-ingest at 06:00 ET.
- **Env vars:** `NEXT_PUBLIC_API_BASE_URL=https://api.health.ironforge.ai`, `API_BASE_URL_INTERNAL=http://backend.railway.internal:8000`. See `.env.local.example`.

Deploy spec: `growthink1/health-metrics-service` → `docs/superpowers/specs/2026-05-18-railway-deploy-design.md`.
```

- [ ] **Step 10.2: Add deploy section to backend README**

Check first:
```bash
ls ~/code/health-metrics-service/README.md
```

If it exists, open it and add a `## Deploy` section before the bottom. If not, create a minimal one:

```markdown
# health-metrics-service

FastAPI backend for Hugo's personal health metrics dashboard. Ingests from Whoop + Oura, computes regulation signals, generates daily narration via Claude.

## Dev

```bash
docker start hms-postgres
source .venv/bin/activate
uvicorn src.health_metrics.main:app --port 8000
```

## Tests

```bash
python3 -m pytest -q
```

## Deploy

Lives on Railway under the `health-metrics` project. Public endpoint at `https://api.health.ironforge.ai` (CF Access gated). Auto-deploys from `main`.

- **Process:** uvicorn (1 worker) + APScheduler in-process (daily ingest at 06:00 ET)
- **DB:** Railway managed Postgres
- **Migrations:** alembic (`alembic upgrade head`)

Deploy spec: `docs/superpowers/specs/2026-05-18-railway-deploy-design.md`.
```

- [ ] **Step 10.3: Commit dashboard README**

```bash
cd ~/code/health-metrics-dashboard
git add README.md
git commit -m "docs: prod deploy URL + env-var split"
git push
```

- [ ] **Step 10.4: Commit backend README**

```bash
cd ~/code/health-metrics-service
git add README.md
git commit -m "docs: prod deploy section"
git push
```

- [ ] **Step 10.5: Tag the backend release**

```bash
cd ~/code/health-metrics-service
git tag -a v0.3.0-railway -m "Railway deploy — backend + APScheduler + /health + Postgres on Railway"
git push --tags
```

- [ ] **Step 10.6: Tag the dashboard release**

```bash
cd ~/code/health-metrics-dashboard
git tag -a v0.1.0-dashboard-frontend -m "Dashboard frontend v1 — Plan 2 + Plan 3 complete; deployed on Railway behind CF Access"
git push --tags
```

- [ ] **Step 10.7: Verify both tags exist**

```bash
cd ~/code/health-metrics-service && git tag -l 'v0.3.*'
cd ~/code/health-metrics-dashboard && git tag -l 'v0.1.*'
```
Expected: each prints exactly one tag.

---

## Validation checklist (Plan 3 exit criteria)

- [ ] `pytest` passes 44+ tests in `health-metrics-service`
- [ ] `npm run test` passes 5 tests + `npm run e2e` passes 3 tests in `health-metrics-dashboard` (e2e may need localhost ports running; run against dev environment, not prod)
- [ ] `npm run build` succeeds on the dashboard
- [ ] `curl https://api.health.ironforge.ai/health` (with CF cookie or service token) returns 200
- [ ] `https://health.ironforge.ai/` after CF Access login renders 30 days of data
- [ ] LogPanel POST persists to Railway Postgres
- [ ] Dashboard SSR calls go via Railway internal network (verify in `railway logs --service backend` — log lines show internal IPs, not CF egress IPs)
- [ ] Both repos tagged: `v0.3.0-railway` on backend, `v0.1.0-dashboard-frontend` on dashboard
- [ ] Scheduler fires the next morning at 06:00 ET (verify via Railway logs ~24h after deploy)

## What's NOT in this plan (deferred)

- Custom-domain TLS via CF Origin Certs (Railway's auto-TLS + CF's orange-cloud is sufficient)
- Off-Railway Postgres backups (Railway's daily backups cover us; revisit if data loss risk warrants belt-and-suspenders)
- Sentry / external error tracking (Railway logs first)
- A `POST /ingest/range` route for ad-hoc backfills (the existing single-day route + a one-shot script suffices)
- `GET /scheduler/status` route (skip until the first time "did the scheduler fire?" comes up)
- Mobile-responsive dashboard layout (already deferred from Plan 2; not part of this deploy)
- Multi-user support / auth UI (CF Access is the only identity layer)
