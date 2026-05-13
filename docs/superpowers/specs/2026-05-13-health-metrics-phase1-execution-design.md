# Health Metrics Service — Phase 1 Execution Design (Steps 1-5)

**Date:** 2026-05-13
**Scope:** Section 11, Steps 1-5 of `docs/spec.md`. Stop before backfill, scheduler, MCP module.
**Master design:** `docs/spec.md` (canonical — this doc only captures session-specific delta)

## Phase 1 deliverables

1. `health-metrics-service` repo scaffolded at `~/code/health-metrics-service`, FastAPI app boots, `/health` returns 200.
2. Postgres schema deployed via Alembic migration into a local Docker Postgres 16 instance. All 4 tables (`daily_metrics`, `workouts`, `manual_log`, `regulation_recommendations`) plus `oauth_state` exist with indexes.
3. Oura v2 client (`sources/oura.py`) — async httpx, all 4 endpoints per spec §4, normalizes to `daily_metrics` shape.
4. Whoop developer v1 client (`sources/whoop.py`) — async httpx with OAuth refresh decorator + token-rotation persistence to `oauth_state`. Bootstrap script committed but unrun.
5. Daily ingest job (`jobs/daily_ingest.py`) — pulls both sources for a date, writes to `daily_metrics` + `workouts`, computes z-scores (returns `NULL` for single-day case — expected).
6. Manual trigger endpoint `POST /ingest/daily?date=YYYY-MM-DD` for ad-hoc invocation.
7. Integration test using fixture playback (`respx`) against a Docker Postgres fixture, asserting one fully-populated row exists after ingest.

## Phase 1 non-deliverables (deferred)

- Backfill endpoint (`POST /backfill?days=30`) — Step 6
- APScheduler wiring — Step 7
- MCP `tools/health/` module in mcp-unified-server — Steps 8-10
- Live Oura/Whoop API calls (no tokens yet)
- Railway deployment (Dockerfile + railway.toml are committed, but no `railway up`)

## Setup decisions

| Decision | Choice | Rationale |
|---|---|---|
| Repo location | `~/code/health-metrics-service` | Matches `~/code/storefront-by-ironforge`, `~/code/tqstarling-platform` |
| Postgres for verification | Local Docker Postgres 16 via `docker-compose.yml` | Fast iteration, no Railway dependency for step 5 |
| API verification | Fixtures + `respx` mock | No tokens yet; live tests deferred to user |
| Whoop OAuth bootstrap | Committed but unrun (`scripts/whoop_oauth_bootstrap.py`) | Ready when client_id/secret land |
| Python version | 3.11 (matches Railway Nixpacks default + spec implied async patterns) | |
| Package manager | `uv` for dev, `pip` for Docker build | uv installs faster locally |

## Conventions matched from `mcp-unified-server`

- `pyproject.toml` with `hatchling` build backend
- structlog configured to stderr — ConsoleRenderer when tty, JSONRenderer when not
- `pydantic-settings` for config, env-prefix `HMS_` (vs `MCP_` for the unified server)
- `Dockerfile` + `railway.toml` (NIXPACKS), `Procfile` for `web:` line
- Tools/modules expose async `execute()` style for MCP layer (relevant Step 8+; not Phase 1)

## Conventions added (not in mcp-unified-server)

- SQLAlchemy 2.x **async** ORM + `asyncpg` driver
- Alembic for migrations
- FastAPI for HTTP layer (mcp-unified-server uses raw Starlette via custom SSE transport — health-metrics-service is a plain web app, FastAPI is the right tool)
- `httpx.AsyncClient` for outbound API calls
- `respx` for HTTP mocking in tests
- `pytest-asyncio` for async test support
- `pytest-postgresql` OR a manual `conftest.py` Docker fixture for ephemeral test DB

## Step 5 verification artifact

`tests/test_jobs_daily_ingest.py::test_single_date_ingest_writes_full_row`:

- Boots ephemeral Postgres DB (separate from dev DB, dropped after run)
- Runs Alembic migrations
- Patches `OuraClient` HTTP calls with `respx` returning fixture JSON for `2026-05-12`
- Patches `WhoopClient` similarly
- Invokes `run_daily_ingest(date=2026-05-12, user_id="hugo")`
- Asserts:
  - Exactly one row in `daily_metrics` with `user_id="hugo"`, `metric_date=2026-05-12`
  - All `oura_*` and `whoop_*` columns populated per fixture mapping
  - `ingestion_complete = TRUE`, `oura_status = "ok"`, `whoop_status = "ok"`
  - `unified_hrv_z`, `unified_rhr_z`, `unified_sleep_z` are `NULL` (insufficient history — expected)
  - At least one row in `workouts` (from Whoop fixture), uniqueness on `(source, source_id)`

A manual smoke verification (out-of-test): `curl -X POST 'http://localhost:8000/ingest/daily?date=2026-05-12'` against the running dev app produces the same row.
