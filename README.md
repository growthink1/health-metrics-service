# health-metrics-service

FastAPI backend for Hugo's personal health metrics dashboard. Ingests from Whoop (v2 API) + Oura, stores in Postgres, computes regulation signals, generates daily narration via Claude.

## Dev

```bash
docker compose up -d postgres
uv sync --extra dev
alembic upgrade head
uvicorn src.health_metrics.main:app --reload --port 8000
```

`config.py` uses `env_ignore_empty=True`, so empty-string env-var shadows fall through to `.env` cleanly — no `env -u ANTHROPIC_API_KEY` workaround needed.

## Tests

```bash
python3 -m pytest -q   # 44 tests
```

## Architecture

- **`src/health_metrics/main.py`** — FastAPI app, mounts `health`, `ingest`, `api` routers, starts the daily-ingest scheduler on startup.
- **`src/health_metrics/jobs/scheduler.py`** — APScheduler with one cron job firing `run_daily_ingest` at 06:00 America/New_York. In-process, only safe with `--workers 1`.
- **`src/health_metrics/jobs/daily_ingest.py`** — pulls Oura + Whoop (v2), upserts `daily_metrics` + `workouts`, recomputes z-scores.
- **`src/health_metrics/routes/api.py`** — 5 REST endpoints: `/api/dashboard/today`, `/api/dashboard/grid`, `/api/metric/{name}`, `/api/workouts`, `/api/manual-log`.
- **`src/health_metrics/routes/health.py`** — `GET /health` with DB liveness probe; Railway uses for restart decisions.
- **`src/health_metrics/db.py`** — async SQLAlchemy engine; normalizes `postgresql://` → `postgresql+asyncpg://` so Railway's auto-injected URL works without an explicit driver prefix.

## Deploy

Live on Railway in the `health-metrics` project as service `backend`. Public URL via `https://backend-production-44b0.up.railway.app` and internal address `backend.railway.internal:8080` (used by the dashboard service over Railway's private network).

- **Process:** `python -m uvicorn` via `Procfile`, 1 worker (APScheduler in-process).
- **DB:** Railway managed Postgres, migrated from local docker `hms-postgres` via `pg_dump --data-only`.
- **Whoop OAuth:** rotating refresh_token lives in `oauth_state` (migrated with the DB); auto-refreshed on 401.
- **Schedule:** daily ingest at 06:00 ET via APScheduler. Idempotent upsert; safe to fire twice.

Env vars (all set via Railway dashboard):
- `DATABASE_URL` (auto-injected from Postgres plugin)
- `USER_ID=hugo`, `TIMEZONE=America/New_York`, `LOG_LEVEL=INFO`
- `WHOOP_CLIENT_ID`, `WHOOP_CLIENT_SECRET`, `WHOOP_REDIRECT_URI`
- `OURA_PERSONAL_TOKEN`
- `ANTHROPIC_API_KEY`, `NARRATION_MODEL=claude-haiku-4-5-20251001`
- `CORS_ALLOWED_ORIGINS=["https://health.ironforgeai.com"]` (harmless leftover — same-origin proxy in dashboard means browser never directly hits this service)
- `PORT=8080`

Deploy spec: [`docs/superpowers/specs/2026-05-18-railway-deploy-design.md`](docs/superpowers/specs/2026-05-18-railway-deploy-design.md).
Implementation plan: [`docs/superpowers/plans/2026-05-18-railway-deploy.md`](docs/superpowers/plans/2026-05-18-railway-deploy.md).
