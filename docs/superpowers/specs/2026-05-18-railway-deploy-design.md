# Plan 3 — Health Metrics Railway Deploy: Design Spec

**Date:** 2026-05-18
**Status:** Approved (architecture); plan to follow
**Repos affected:** `growthink1/health-metrics-service` + `growthink1/health-metrics-dashboard`

## Goal

Move `health-metrics-service` (FastAPI backend) and `health-metrics-dashboard` (Next.js frontend) from localhost-only to a production deployment on Railway, accessible only to Hugo via Cloudflare Access at `health.ironforge.ai`. Migrate the existing local Postgres dataset (30-day backfill + Whoop OAuth state + Hugo's manual logs) so the prod dashboard renders real data on day one. Run the daily Whoop+Oura ingest automatically in-process.

## Non-goals

- Multi-user. This is single-user (Hugo). No login UI in the app itself; CF Access is the only identity layer.
- Mobile-responsive layout. Desktop-only matches the Plan 2 spec.
- Custom client-side write rate-limiting beyond what Cloudflare already provides.
- Sentry, Datadog, or third-party error tracking. Railway logs are sufficient.
- Scheduled off-Railway database backups. Railway's automatic Postgres backups (paid plan) cover us.
- Drift-detection, multi-region, auto-scaling. Single instance per service.

## Architecture

```
                                 Cloudflare Access (Google login → hdelgad2@alumni.nd.edu)
                                              │
                                              ▼
                              ┌───────────────────────────────┐
                              │  health.ironforge.ai          │
                              │  api.health.ironforge.ai      │
                              └───────────────┬───────────────┘
                                              │
                                  ┌───────────┴──────────┐
                                  │  Railway Project     │
                                  │  "health-metrics"    │
                                  │                      │
                              ┌───┴────┐           ┌─────┴────┐
                              │Dashboard│──SSR────▶│ Backend  │
                              │(Next.js)│  via     │(FastAPI) │
                              │         │  Railway │  +       │
                              │  ▲      │  private │  APSched │
                              └──┼──────┘  network └────┬─────┘
                                 │                      │
                                 │ CF-cookie auth       │
                                 │ from browser         │
                              [Hugo]              ┌─────┴─────┐
                                                  │ Postgres  │
                                                  │ (managed) │
                                                  └───────────┘
```

**Two Railway services + one managed Postgres, all in the same project.**

- Cloudflare Access gates both public subdomains. The CF Access cookie is scoped to `.ironforge.ai`, so logging into either subdomain auths both.
- Dashboard SSR fetches the backend over Railway's internal IPv6 private network (`http://${BACKEND_SERVICE}.railway.internal:8000`) — never traverses CF, never re-auths.
- Dashboard client-side fetches (LogPanel POST, navigation triggered re-fetches if any) go to the public `api.health.ironforge.ai` URL; the browser already has a `CF_Authorization` cookie from logging into the dashboard, so CF Access passes the request through.

## Components

### 1. Backend deploy artifacts (`health-metrics-service`)

**New file: `Procfile`**
```
web: uvicorn src.health_metrics.main:app --host 0.0.0.0 --port $PORT --workers 1
```

`--workers 1` is intentional — APScheduler runs in-process and we want exactly one scheduler, not one per worker. For a single-user dashboard the request volume is fine with one worker.

**New route: `GET /health`**
- Returns `{"status": "ok", "db": "ok"}` after a `SELECT 1` against Postgres
- Returns 503 if the DB connection fails
- Railway pings this; CF uses it for upstream health
- ~15 lines + a small test using the existing test_user_id fixture

**New module: `src/health_metrics/jobs/scheduler.py`**
- Uses `apscheduler.schedulers.asyncio.AsyncIOScheduler`
- One cron job: daily at 06:00 America/New_York, calls `run_daily_ingest(yesterday, settings.user_id, session)` for today-1 in the configured timezone
- Started from `main.py` on app startup (`@app.on_event("startup")`), shut down on `@app.on_event("shutdown")`
- Idempotent — running twice in one day just upserts the same daily_metrics row
- ~40 lines + a unit test that asserts the scheduler is registered (does NOT run the actual cron in tests)

**New env var: none new beyond what's already in `.env.example`.** The scheduler config is hard-coded (06:00 ET); CORS, OAuth, narration model env vars stay the same.

### 2. Dashboard refactor (`health-metrics-dashboard`)

**Modified: `lib/api.ts`** — dual-URL pattern:
```ts
const isServer = typeof window === 'undefined';
const API_BASE = isServer
  ? (process.env.API_BASE_URL_INTERNAL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000")
  : (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000");
```

- `API_BASE_URL_INTERNAL` (server-only env, NOT prefixed with `NEXT_PUBLIC_`) is the Railway internal URL: `http://backend.railway.internal:8000`. Not exposed to the browser.
- `NEXT_PUBLIC_API_BASE_URL` is the public URL: `https://api.health.ironforge.ai`. Exposed in client bundles; that's fine because CF Access protects it.
- Fallback to localhost preserves dev-mode behavior.

**New `.env.local.example` line:** `API_BASE_URL_INTERNAL=http://localhost:8000` (matches dev — same host, just labeled).

### 3. Railway project layout

Single project: `health-metrics`. Three resources:

| Resource | Type | Source | Internal hostname |
|---|---|---|---|
| `backend` | service | `growthink1/health-metrics-service` GitHub | `backend.railway.internal` |
| `dashboard` | service | `growthink1/health-metrics-dashboard` GitHub | `dashboard.railway.internal` |
| `postgres` | managed DB | Railway template | — |

Railway auto-injects `DATABASE_URL` into the backend service from the Postgres plugin. All other env vars are set manually via `railway variables set`.

### 4. Postgres migration

One-shot at deploy time, from the local `hms-postgres` docker container to Railway's managed Postgres:

```bash
# Locally:
docker exec hms-postgres pg_dump -U hms -d health_metrics --no-owner --no-acl > /tmp/hms_dump.sql

# Get Railway DATABASE_URL (must be the "public" connection string for one-shot import):
RAILWAY_DATABASE_URL=$(railway variables --service postgres | grep DATABASE_URL_PUBLIC | cut -d= -f2-)

# Migrations first (creates schema):
DATABASE_URL=$RAILWAY_DATABASE_URL alembic upgrade head

# Then data:
psql "$RAILWAY_DATABASE_URL" < /tmp/hms_dump.sql
```

Data preserved: 30 days of `daily_metrics` (just backfilled), 1+ rotating refresh tokens in `oauth_state`, narration_cache (regenerates anyway, but no harm), manual_log (currently empty after the T11 cleanup — Hugo will populate via prod UI).

The local schema currently has no seed rows (the only data is what Hugo's ingest and backfill produced), so `pg_dump --no-owner --no-acl` + plain `psql` import should not trip duplicate-key conflicts against an empty alembic-migrated Railway DB. If a conflict does surface at T7, the fallback is `pg_dump --data-only --inserts` (one row per INSERT, safer for partial recovery) — handled inline, no separate task.

### 5. Cloudflare configuration

Assumes `ironforge.ai` is on Cloudflare. Two CNAMEs:
- `health` → `<dashboard-railway-url>.up.railway.app` (proxied, orange-cloud ON)
- `api.health` → `<backend-railway-url>.up.railway.app` (proxied, orange-cloud ON)

Two Cloudflare Access applications, both with:
- **Policy:** Allow, email matches `hdelgad2@alumni.nd.edu`
- **Session duration:** 30 days
- **Cookie scope:** `.ironforge.ai` (so a single login covers both)
- **Identity provider:** Google (matches Hugo's `hdelgad2@alumni.nd.edu` Google account)

### 6. Whoop OAuth handling in prod

The `oauth_state` row migrated from local Postgres carries the rotating refresh_token. Whoop refresh tokens aren't bound to `redirect_uri`, so the existing token keeps working in prod.

Set `WHOOP_REDIRECT_URI=https://api.health.ironforge.ai/whoop/callback` in the backend env vars for completeness. This URL is only exercised if Hugo ever re-bootstraps OAuth from scratch (which he won't unless the refresh token gets revoked).

In the Whoop developer dashboard, add the prod callback URL to the app's allowed redirect URIs alongside `http://localhost:8000/whoop/callback`. Without that, a future re-bootstrap from prod would fail.

## Data flow examples

**1. User opens https://health.ironforge.ai (cold cache)**
1. CF Access checks cookie — no cookie → redirect to Google login → cookie set, scope=`.ironforge.ai`
2. CF Access forwards request to Railway dashboard service
3. Next.js server component `app/page.tsx` calls `fetchDashboardToday()` from `lib/api.ts`
4. `isServer === true` → uses `API_BASE_URL_INTERNAL` = `http://backend.railway.internal:8000`
5. Request hits backend over Railway's IPv6 private network (no CF in path)
6. Backend queries Postgres, generates narration via Anthropic, returns JSON
7. SSR completes, HTML streams to browser

**2. LogPanel POST**
1. User clicks "Save" in the LogPanel client component
2. `postManualLog()` runs in the browser
3. `isServer === false` → uses `NEXT_PUBLIC_API_BASE_URL` = `https://api.health.ironforge.ai`
4. Browser already has `CF_Authorization` cookie on `.ironforge.ai` from step 1.2 above
5. CF Access verifies cookie, forwards to backend
6. Backend upserts the row, returns 200
7. `router.refresh()` triggers a fresh SSR (loop back to flow 1, skip CF login)

**3. Daily ingest (autonomous)**
1. APScheduler fires at 06:00 America/New_York (=10:00 UTC during EDT, 11:00 UTC during EST)
2. Calls `run_daily_ingest(yesterday, settings.user_id, session)` in-process
3. Whoop client makes v2 API calls using stored refresh_token (auto-refreshes if expired)
4. Oura client makes calls using personal token
5. Rows upserted into daily_metrics + workouts + narration_cache (lazy on next dashboard fetch)
6. Log lines visible in Railway logs

## Error handling

- **Backend can't reach Postgres:** `/health` returns 503, Railway marks unhealthy, restarts service automatically.
- **Whoop refresh token revoked:** `whoop_status: "auth_failed"` in ingest result. Logged. Dashboard still renders (no new data, but historical data is fine). Hugo notices via Railway log alerts (or by looking at the dashboard's `metric_date` going stale) and re-bootstraps OAuth manually.
- **Anthropic API down:** narration returns `null`, dashboard renders the fallback "Narration unavailable" line. Already implemented.
- **Dashboard can't reach backend (internal network blip):** Next.js error boundary (`app/error.tsx` from T10) shows a retry button.
- **CF Access misconfigured / Google login fails:** CF returns its own error page. Out of our control.

## Testing strategy

**Unit + integration (existing 40 tests):** All keep passing with no changes — none of them depend on hosting.

**New tests** for the backend:
- `test_health_endpoint_returns_200_when_db_up` — uses `db_session` fixture
- `test_health_endpoint_returns_503_when_db_down` — monkeypatches the session factory to raise
- `test_scheduler_registers_daily_job` — asserts `AsyncIOScheduler.get_jobs()` contains the daily-ingest cron with correct trigger
- (No test that actually runs the cron — APScheduler timing tests are flaky and not worth it for a one-job scheduler)

**Smoke tests at deploy time** (T8):
- `curl https://api.health.ironforge.ai/health` → expect 200 (after CF Access auth via service token or browser cookie copy)
- Open `https://health.ironforge.ai/` in a browser → expect CF login → dashboard renders with 30-day data
- POST a real subjective entry via the LogPanel → expect the row in Railway Postgres
- Wait until 06:00 ET the next morning → confirm the scheduler fired (check Railway logs for `daily_ingest_complete`)

## Task list (handoff to writing-plans)

| # | Task | Repo | Est. |
|---|---|---|---|
| T1 | Backend: `GET /health` endpoint + test | service | 20m |
| T2 | Backend: `Procfile` + verify `uvicorn` start command works locally | service | 10m |
| T3 | Backend: APScheduler daily-ingest job + test | service | 45m |
| T4 | Dashboard: dual-URL refactor in `lib/api.ts` + `.env.local.example` update | dashboard | 20m |
| T5 | Railway project + Postgres + backend service + dashboard service (CLI) | infra | 30m |
| T6 | Set env vars on both services (Whoop OAuth, ANTHROPIC_API_KEY rotated, CORS, internal URL, etc.) | infra | 15m |
| T7 | Run alembic migrations + pg_dump-from-local / psql-to-Railway data load | infra | 15m |
| T8 | Cloudflare DNS + Access policies on both subdomains | infra | 15m |
| T9 | End-to-end smoke (browser open, login, dashboard renders, LogPanel POST) | infra | 15m |
| T10 | Tag dashboard `v0.1.0-dashboard-frontend` + backend `v0.3.0-railway` + README updates | both | 15m |

**Total est:** ~3.5h focused work + Cloudflare/Whoop dashboard clicks Hugo handles inline.

## Out-of-plan followups (track for after Plan 3 ships)

- **APScheduler observability:** add a small route `GET /scheduler/status` returning last-run timestamp + result. Defer until first time you wonder "did ingest fire?".
- **Custom domain TLS via CF origin certs:** Railway already provides TLS for its `*.up.railway.app` URLs; CF re-encrypts on the way out. Both endpoints serve HTTPS end-to-end. Nothing to add.
- **Multi-day backfill via prod API:** if you ever need to re-ingest, add a `POST /ingest/range?start=...&end=...` route. Skipped for now — local backfill served the same purpose.
- **Sentry / structured error alerts:** maybe later if Railway logs prove insufficient.
