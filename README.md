# health-metrics-service

Daily Oura + Whoop ingestion to Postgres. Feeds the `tools/health/` MCP module in `mcp-unified-server`.

See `docs/spec.md` for the full design.

## Quick start (dev)

    docker compose up -d postgres
    uv sync --extra dev
    alembic upgrade head
    uvicorn src.health_metrics.main:app --reload

## Phase 1 status

Steps 1-5 of `docs/spec.md` §11 are in this branch. Backfill, scheduler, and MCP tool module land in Phase 2.
