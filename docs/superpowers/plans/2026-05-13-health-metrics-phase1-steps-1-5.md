# Health Metrics Service — Phase 1 Implementation Plan (Steps 1-5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `health-metrics-service` repo, deploy Postgres schema, implement Oura + Whoop API clients, and verify a single-date daily ingest writes a fully-populated row to `daily_metrics` and matching workouts to `workouts`.

**Architecture:** FastAPI app on uvicorn, async SQLAlchemy 2.x over asyncpg to Postgres 16, httpx.AsyncClient for outbound API calls, Alembic migrations, fixture-driven tests via `respx`. Local Docker Postgres for dev + test. Follows `growthink1/mcp-unified-server` conventions (structlog-to-stderr, pydantic-settings, hatchling, NIXPACKS+Railway) with FastAPI + Alembic added for this service's needs.

**Tech Stack:** Python 3.11, FastAPI 0.110+, uvicorn, SQLAlchemy 2.0+ (async), asyncpg, Alembic, httpx, pydantic 2.x, pydantic-settings, structlog, APScheduler (imported only, wired in Phase 2), pytest-asyncio, respx, pytest-postgresql or manual docker fixture.

**Stop condition:** Phase 1 complete when Task 11 integration test passes AND a manual `POST /ingest/daily?date=2026-05-12` against the running app produces the same row. Do not start backfill (Step 6), scheduler (Step 7), or MCP tools (Step 8+).

---

## File structure (created in this phase)

```
~/code/health-metrics-service/
├── .env.example
├── .gitignore
├── .python-version              # 3.11
├── Dockerfile
├── Procfile
├── README.md
├── alembic.ini
├── docker-compose.yml            # Postgres 16 for local dev
├── pyproject.toml
├── railway.toml
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py
├── docs/
│   ├── spec.md                                                    # already exists
│   └── superpowers/
│       ├── specs/2026-05-13-health-metrics-phase1-execution-design.md  # already exists
│       └── plans/2026-05-13-health-metrics-phase1-steps-1-5.md         # this file
├── scripts/
│   └── whoop_oauth_bootstrap.py
├── src/
│   └── health_metrics/
│       ├── __init__.py
│       ├── main.py                  # FastAPI app entry, logging setup
│       ├── config.py                # Pydantic Settings (HMS_ prefix)
│       ├── db.py                    # Async engine + sessionmaker
│       ├── models.py                # SQLAlchemy ORM (5 tables)
│       ├── schemas.py               # Pydantic request/response models
│       ├── routes/
│       │   ├── __init__.py
│       │   ├── health.py            # GET /health
│       │   └── ingest.py            # POST /ingest/daily?date=...
│       ├── sources/
│       │   ├── __init__.py
│       │   ├── base.py              # SourceClient ABC (or just protocol)
│       │   ├── oura.py              # OuraClient
│       │   └── whoop.py             # WhoopClient + OAuth refresh
│       ├── transforms/
│       │   ├── __init__.py
│       │   ├── normalize.py         # API payload → ORM row dict
│       │   └── zscore.py            # 14d rolling z-score
│       └── jobs/
│           ├── __init__.py
│           └── daily_ingest.py      # run_daily_ingest(date, user_id)
└── tests/
    ├── __init__.py
    ├── conftest.py                  # Docker postgres fixture + asyncio loop
    ├── fixtures/
    │   ├── __init__.py
    │   ├── oura_responses.json
    │   └── whoop_responses.json
    ├── test_config.py
    ├── test_models_migration.py
    ├── test_sources_oura.py
    ├── test_sources_whoop.py
    ├── test_transforms_normalize.py
    ├── test_transforms_zscore.py
    └── test_jobs_daily_ingest.py
```

---

## Task 1: Scaffold repo + pyproject + git init + initial commit

**Files:**
- Create: `~/code/health-metrics-service/pyproject.toml`
- Create: `~/code/health-metrics-service/.gitignore`
- Create: `~/code/health-metrics-service/.python-version`
- Create: `~/code/health-metrics-service/README.md`
- Create: `~/code/health-metrics-service/.env.example`
- Create: `~/code/health-metrics-service/src/health_metrics/__init__.py`

- [ ] **Step 1.1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "health-metrics-service"
version = "0.1.0"
description = "Daily Oura + Whoop ingestion to Postgres for Hugo's auto-regulated training/nutrition planning"
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
authors = [{ name = "Hugo Delgado", email = "hdelgad2@alumni.nd.edu" }]
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "sqlalchemy[asyncio]>=2.0.25",
    "asyncpg>=0.29.0",
    "alembic>=1.13.0",
    "httpx>=0.27.0",
    "pydantic>=2.6.0",
    "pydantic-settings>=2.2.0",
    "structlog>=24.1.0",
    "apscheduler>=3.10.4",
    "tzdata>=2024.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "respx>=0.20.2",
    "ruff>=0.3.0",
]

[tool.hatch.build.targets.wheel]
packages = ["src/health_metrics"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "ASYNC"]
```

> Note: `pydantic-settings` loads `.env` natively — no separate `python-dotenv` dependency is needed, and `main.py` does NOT call `load_dotenv()`.

- [ ] **Step 1.2: Write `.gitignore`**

```
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.env
.env.local
*.db
.pytest_cache/
.ruff_cache/
dist/
build/
.coverage
htmlcov/
```

- [ ] **Step 1.3: Write `.python-version`**

```
3.11
```

- [ ] **Step 1.4: Write `README.md`**

```markdown
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
```

- [ ] **Step 1.5: Write `.env.example`**

```
# Database
DATABASE_URL=postgresql+asyncpg://hms:hms_dev_password@localhost:5432/health_metrics

# User
USER_ID=hugo
TIMEZONE=America/New_York

# Logging
LOG_LEVEL=INFO

# Oura
OURA_PERSONAL_TOKEN=

# Whoop
WHOOP_CLIENT_ID=
WHOOP_CLIENT_SECRET=
WHOOP_REFRESH_TOKEN=
WHOOP_REDIRECT_URI=http://localhost:8000/whoop/callback
```

- [ ] **Step 1.6: Write empty `src/health_metrics/__init__.py`**

```python
"""health-metrics-service: Oura + Whoop ingestion pipeline."""

__version__ = "0.1.0"
```

- [ ] **Step 1.7: Initialize git + first commit**

```bash
cd ~/code/health-metrics-service
git init -b main
git add .
git commit -m "chore: scaffold health-metrics-service repo"
```

Expected: First commit lands. Repo structure ready for code.

---

## Task 2: Docker Compose Postgres + verify connection

**Files:**
- Create: `~/code/health-metrics-service/docker-compose.yml`
- Create: `~/code/health-metrics-service/.env` (gitignored — local dev only)

- [ ] **Step 2.1: Write `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    container_name: hms-postgres
    environment:
      POSTGRES_USER: hms
      POSTGRES_PASSWORD: hms_dev_password
      POSTGRES_DB: health_metrics
    ports:
      - "5433:5432"
    volumes:
      - hms_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "hms", "-d", "health_metrics"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  hms_pgdata:
```

> Port `5433` (not 5432) — Hugo's machine already runs a system Postgres on 5432. The container still listens on 5432 internally; only the host-side port is remapped.

- [ ] **Step 2.2: Create local `.env` (copy of .env.example with real DATABASE_URL)**

```bash
cp .env.example .env
```

- [ ] **Step 2.3: Start Postgres + verify**

```bash
docker compose up -d postgres
# Wait for healthcheck
until docker compose ps postgres | grep -q "healthy"; do sleep 2; done
docker exec hms-postgres psql -U hms -d health_metrics -c "SELECT version();"
```

Expected: Returns Postgres 16 version string.

- [ ] **Step 2.4: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: add docker-compose Postgres for local dev"
```

---

## Task 3: Config module + structlog setup

**Files:**
- Create: `src/health_metrics/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 3.1: Write failing test `tests/test_config.py`**

```python
import os
from health_metrics.config import get_settings


def test_settings_loads_database_url(monkeypatch):
    monkeypatch.setenv("HMS_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("HMS_USER_ID", "testuser")
    s = get_settings()
    assert s.database_url == "postgresql+asyncpg://test:test@localhost/test"
    assert s.user_id == "testuser"


def test_settings_defaults_timezone_and_log_level(monkeypatch):
    monkeypatch.setenv("HMS_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    s = get_settings()
    assert s.timezone == "America/New_York"
    assert s.log_level == "INFO"
```

Also create `tests/__init__.py` (empty).

- [ ] **Step 3.2: Run test → expect ImportError**

```bash
cd ~/code/health-metrics-service
pip install -e ".[dev]"   # or `uv sync --extra dev`
pytest tests/test_config.py -v
```

Expected: ImportError on `health_metrics.config`.

- [ ] **Step 3.3: Update `tests/test_config.py` to use unprefixed env names**

Replace the test file from Step 3.1 with this version (env names match `.env.example` — the spec's source of truth and what Railway will use):

```python
from health_metrics.config import get_settings


def test_settings_loads_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("USER_ID", "testuser")
    get_settings.cache_clear()
    s = get_settings()
    assert s.database_url == "postgresql+asyncpg://test:test@localhost/test"
    assert s.user_id == "testuser"


def test_settings_defaults_timezone_and_log_level(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    get_settings.cache_clear()
    s = get_settings()
    assert s.timezone == "America/New_York"
    assert s.log_level == "INFO"
```

- [ ] **Step 3.4: Write `src/health_metrics/config.py`** — env vars unprefixed (matches `.env.example` and Railway convention)

```python
"""Configuration loaded from environment variables."""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        ...,
        description="Async SQLAlchemy connection string, e.g. postgresql+asyncpg://user:pw@host/db",
    )

    # Identity & locale
    user_id: str = Field(default="hugo")
    timezone: str = Field(default="America/New_York")

    # Logging
    log_level: str = Field(default="INFO")

    # Oura
    oura_personal_token: Optional[str] = Field(default=None)
    oura_base_url: str = Field(default="https://api.ouraring.com/v2")

    # Whoop
    whoop_client_id: Optional[str] = Field(default=None)
    whoop_client_secret: Optional[str] = Field(default=None)
    whoop_refresh_token: Optional[str] = Field(default=None)
    whoop_redirect_uri: str = Field(default="http://localhost:8000/whoop/callback")
    whoop_base_url: str = Field(default="https://api.prod.whoop.com/developer/v1")
    whoop_oauth_url: str = Field(default="https://api.prod.whoop.com/oauth/oauth2/token")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 3.5: Run tests → expect PASS**

```bash
pytest tests/test_config.py -v
```

Expected: 2 passed.

- [ ] **Step 3.6: Commit**

```bash
git add src/health_metrics/config.py tests/test_config.py tests/__init__.py
git commit -m "feat: pydantic-settings config module"
```

---

## Task 4: FastAPI app entry + /health endpoint + structlog wiring

**Files:**
- Create: `src/health_metrics/main.py`
- Create: `src/health_metrics/routes/__init__.py` (empty)
- Create: `src/health_metrics/routes/health.py`

- [ ] **Step 4.1: Write `src/health_metrics/routes/__init__.py`** (empty file)

- [ ] **Step 4.2: Write `src/health_metrics/routes/health.py`**

```python
"""Health check route."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 4.3: Write `src/health_metrics/main.py`**

```python
"""FastAPI app entry — health-metrics-service."""

import sys

import structlog
from fastapi import FastAPI

from .config import get_settings
from .routes import health as health_route


def configure_logging(log_level: str) -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer()
            if sys.stderr.isatty()
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


settings = get_settings()
configure_logging(settings.log_level)
log = structlog.get_logger()

app = FastAPI(title="health-metrics-service", version="0.1.0")
app.include_router(health_route.router)

log.info("app_initialized", version="0.1.0", user_id=settings.user_id)
```

- [ ] **Step 4.4: Boot app + curl /health**

```bash
uvicorn src.health_metrics.main:app --port 8000 &
UV_PID=$!
sleep 2
curl -s http://localhost:8000/health
kill $UV_PID
```

Expected: `{"status":"ok"}`.

- [ ] **Step 4.5: Commit**

```bash
git add src/health_metrics/main.py src/health_metrics/routes/
git commit -m "feat: FastAPI app + /health endpoint + structlog wiring"
```

---

## Task 5: SQLAlchemy models — all 5 tables

**Files:**
- Create: `src/health_metrics/db.py`
- Create: `src/health_metrics/models.py`

- [ ] **Step 5.1: Write `src/health_metrics/db.py`**

```python
"""Async SQLAlchemy engine + session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False, future=True)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```

- [ ] **Step 5.2: Write `src/health_metrics/models.py`**

```python
"""SQLAlchemy ORM models — mirrors docs/spec.md §3 schema."""

from datetime import date as date_type, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class DailyMetrics(Base):
    __tablename__ = "daily_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    metric_date: Mapped[date_type] = mapped_column(Date, nullable=False)

    # Oura
    oura_sleep_score: Mapped[Optional[int]] = mapped_column(Integer)
    oura_sleep_duration_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_sleep_efficiency: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    oura_sleep_latency_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_rem_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_deep_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_light_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_awake_min: Mapped[Optional[int]] = mapped_column(Integer)
    oura_hrv_avg: Mapped[Optional[int]] = mapped_column(Integer)
    oura_rhr: Mapped[Optional[int]] = mapped_column(Integer)
    oura_temp_deviation: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2))
    oura_readiness_score: Mapped[Optional[int]] = mapped_column(Integer)
    oura_raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    # Whoop
    whoop_recovery_score: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_hrv_ms: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    whoop_rhr: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_sleep_performance: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_sleep_need_min: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_sleep_debt_min: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_day_strain: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2))
    whoop_avg_hr: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_max_hr: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_kcal_burned: Mapped[Optional[int]] = mapped_column(Integer)
    whoop_raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    # Derived
    unified_hrv_z: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    unified_rhr_z: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    unified_sleep_z: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    ingestion_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    oura_status: Mapped[Optional[str]] = mapped_column(Text)
    whoop_status: Mapped[Optional[str]] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        UniqueConstraint("user_id", "metric_date", name="uq_daily_metrics_user_date"),
        Index("idx_daily_metrics_user_date", "user_id", "metric_date"),
    )


class Workout(Base):
    __tablename__ = "workouts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    workout_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    workout_type: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_hr: Mapped[Optional[int]] = mapped_column(Integer)
    max_hr: Mapped[Optional[int]] = mapped_column(Integer)
    strain: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2))
    kcal: Mapped[Optional[int]] = mapped_column(Integer)
    zone_0_min: Mapped[Optional[int]] = mapped_column(Integer)
    zone_1_min: Mapped[Optional[int]] = mapped_column(Integer)
    zone_2_min: Mapped[Optional[int]] = mapped_column(Integer)
    zone_3_min: Mapped[Optional[int]] = mapped_column(Integer)
    zone_4_min: Mapped[Optional[int]] = mapped_column(Integer)
    zone_5_min: Mapped[Optional[int]] = mapped_column(Integer)
    raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_workouts_source_sourceid"),
        Index("idx_workouts_user_date", "user_id", "workout_date"),
        Index("idx_workouts_type", "workout_type"),
    )


class ManualLog(Base):
    __tablename__ = "manual_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    log_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    weight_lbs: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    kcal_consumed: Mapped[Optional[int]] = mapped_column(Integer)
    protein_g: Mapped[Optional[int]] = mapped_column(Integer)
    fat_g: Mapped[Optional[int]] = mapped_column(Integer)
    carbs_g: Mapped[Optional[int]] = mapped_column(Integer)
    subjective_energy: Mapped[Optional[int]] = mapped_column(Integer)
    subjective_mood: Mapped[Optional[int]] = mapped_column(Integer)
    subjective_hunger: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        UniqueConstraint("user_id", "log_date", name="uq_manual_log_user_date"),
    )


class RegulationRecommendation(Base):
    __tablename__ = "regulation_recommendations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    rec_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    rec_type: Mapped[Optional[str]] = mapped_column(Text)
    suggested_kcal: Mapped[Optional[int]] = mapped_column(Integer)
    suggested_training_mod: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[str]] = mapped_column(Text)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    triggering_signals: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_reg_rec_user_date", "user_id", "rec_date"),
    )


class OAuthState(Base):
    """Persists rotating refresh tokens for Whoop OAuth (Gotcha #3)."""

    __tablename__ = "oauth_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    access_token: Mapped[Optional[str]] = mapped_column(Text)
    access_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        UniqueConstraint("provider", "user_id", name="uq_oauth_state_provider_user"),
    )
```

- [ ] **Step 5.3: Smoke-import the models**

```bash
python -c "from health_metrics import models; print([t.name for t in models.Base.metadata.sorted_tables])"
```

Expected output:
```
['daily_metrics', 'manual_log', 'oauth_state', 'regulation_recommendations', 'workouts']
```

- [ ] **Step 5.4: Commit**

```bash
git add src/health_metrics/db.py src/health_metrics/models.py
git commit -m "feat: SQLAlchemy async engine + ORM models for 5 tables"
```

---

## Task 6: Alembic init + initial migration + apply against Docker Postgres

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/0001_initial_schema.py`
- Test: `tests/test_models_migration.py`

- [ ] **Step 6.1: Run `alembic init alembic` then customize**

```bash
cd ~/code/health-metrics-service
alembic init alembic
```

This generates `alembic.ini` and `alembic/`. Then edit:

**`alembic.ini`** — change `sqlalchemy.url` to empty (we set it programmatically) and `script_location` to `alembic`. Replace top portion:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
version_path_separator = os
sqlalchemy.url =

[post_write_hooks]

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 6.2: Replace `alembic/env.py`**

```python
"""Alembic env — async + reads DATABASE_URL from settings."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from health_metrics.config import get_settings
from health_metrics.models import Base  # noqa: F401 — registers all models

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 6.3: Autogenerate the initial migration**

```bash
docker compose up -d postgres
until docker compose ps postgres | grep -q "healthy"; do sleep 2; done
alembic revision --autogenerate -m "initial schema"
```

Expected: A file appears at `alembic/versions/<hash>_initial_schema.py`. Rename it to `0001_initial_schema.py` (keep the autogenerated content), or just leave the auto-hash filename — both work. Then inspect it and confirm it creates `daily_metrics`, `workouts`, `manual_log`, `regulation_recommendations`, `oauth_state` with all columns + indexes + unique constraints.

If autogenerate misses any indexes or default values, hand-edit the migration. Compare against `docs/spec.md` §3.

- [ ] **Step 6.4: Apply migration**

```bash
alembic upgrade head
```

Expected: "Running upgrade  -> 0001..., initial schema".

- [ ] **Step 6.5: Verify tables exist via psql**

```bash
docker exec hms-postgres psql -U hms -d health_metrics -c "\dt"
```

Expected: lists `alembic_version`, `daily_metrics`, `manual_log`, `oauth_state`, `regulation_recommendations`, `workouts`.

- [ ] **Step 6.6: Write `tests/test_models_migration.py`** (sanity check the migration logic separately from live DB; this test asserts model metadata shape)

```python
from health_metrics.models import Base


def test_all_expected_tables_registered():
    tables = {t.name for t in Base.metadata.sorted_tables}
    assert tables == {
        "daily_metrics",
        "workouts",
        "manual_log",
        "regulation_recommendations",
        "oauth_state",
    }


def test_daily_metrics_unique_constraint():
    t = Base.metadata.tables["daily_metrics"]
    uqs = {c.name for c in t.constraints if c.__class__.__name__ == "UniqueConstraint"}
    assert "uq_daily_metrics_user_date" in uqs


def test_workouts_source_uniqueness():
    t = Base.metadata.tables["workouts"]
    uqs = {c.name for c in t.constraints if c.__class__.__name__ == "UniqueConstraint"}
    assert "uq_workouts_source_sourceid" in uqs
```

- [ ] **Step 6.7: Run model tests**

```bash
pytest tests/test_models_migration.py -v
```

Expected: 3 passed.

- [ ] **Step 6.8: Commit**

```bash
git add alembic.ini alembic/ tests/test_models_migration.py
git commit -m "feat: alembic initial schema migration"
```

---

## Task 7: Test infrastructure — Docker Postgres conftest fixture

**Files:**
- Create: `tests/conftest.py`

This fixture gives every integration test a clean DB. We reuse the running Docker Postgres but each test gets a unique schema or transactional rollback.

Strategy: per-test transactional rollback against the dev DB. Simpler than spinning up new containers per test.

- [ ] **Step 7.1: Write `tests/conftest.py`**

```python
"""Pytest fixtures — async DB session with per-test rollback."""

import asyncio
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from health_metrics.config import get_settings
from health_metrics.models import Base


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False, future=True)
    # Ensure schema is up — assumes alembic upgrade head was already run.
    # If you want each test session to be hermetic, uncomment:
    # async with engine.begin() as conn:
    #     await conn.run_sync(Base.metadata.drop_all)
    #     await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    """Per-test session that rolls back on teardown."""
    connection = await db_engine.connect()
    transaction = await connection.begin()
    SessionLocal = async_sessionmaker(
        bind=connection, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    session = SessionLocal()
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
```

- [ ] **Step 7.2: Quick sanity test (inline)**

Add to a new file `tests/test_conftest_sanity.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_db_session_works(db_session):
    from sqlalchemy import text
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1
```

```bash
pytest tests/test_conftest_sanity.py -v
```

Expected: 1 passed.

- [ ] **Step 7.3: Commit**

```bash
git add tests/conftest.py tests/test_conftest_sanity.py
git commit -m "test: per-test transactional rollback fixture"
```

---

## Task 8: Oura client + fixtures + tests

**Files:**
- Create: `src/health_metrics/sources/__init__.py` (empty)
- Create: `src/health_metrics/sources/base.py`
- Create: `src/health_metrics/sources/oura.py`
- Create: `tests/fixtures/__init__.py` (empty)
- Create: `tests/fixtures/oura_responses.json`
- Create: `tests/test_sources_oura.py`

- [ ] **Step 8.1: Write `src/health_metrics/sources/base.py`**

```python
"""Common types for source clients."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class OuraDayPayload:
    """Normalized Oura payload for one date."""

    metric_date: date
    sleep_score: int | None = None
    sleep_duration_min: int | None = None
    sleep_efficiency: float | None = None
    sleep_latency_min: int | None = None
    rem_min: int | None = None
    deep_min: int | None = None
    light_min: int | None = None
    awake_min: int | None = None
    hrv_avg: int | None = None
    rhr: int | None = None
    temp_deviation: float | None = None
    readiness_score: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WhoopDayPayload:
    """Normalized Whoop payload for one date."""

    metric_date: date
    recovery_score: int | None = None
    hrv_ms: float | None = None
    rhr: int | None = None
    sleep_performance: int | None = None
    sleep_need_min: int | None = None
    sleep_debt_min: int | None = None
    day_strain: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    kcal_burned: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WhoopWorkout:
    source_id: str
    workout_date: date
    workout_type: str | None
    started_at: str  # ISO with tz
    duration_min: int
    avg_hr: int | None
    max_hr: int | None
    strain: float | None
    kcal: int | None
    zone_minutes: dict[int, int] = field(default_factory=dict)  # zone_idx -> minutes
    raw: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 8.2: Write `tests/fixtures/oura_responses.json`**

This is synthesized fixture data matching the Oura v2 API shape for 2026-05-12. Field names per `docs/spec.md` §4.

```json
{
  "2026-05-12": {
    "daily_sleep": {
      "data": [
        {
          "id": "ds-2026-05-12",
          "day": "2026-05-12",
          "score": 78,
          "timestamp": "2026-05-12T08:00:00+00:00"
        }
      ],
      "next_token": null
    },
    "sleep": {
      "data": [
        {
          "id": "s-2026-05-12-main",
          "day": "2026-05-12",
          "bedtime_start": "2026-05-11T23:30:00-04:00",
          "bedtime_end": "2026-05-12T07:15:00-04:00",
          "total_sleep_duration": 24720,
          "awake_time": 1080,
          "rem_sleep_duration": 5040,
          "deep_sleep_duration": 4200,
          "light_sleep_duration": 15480,
          "efficiency": 89.2,
          "latency": 540,
          "average_hrv": 45,
          "lowest_heart_rate": 58,
          "type": "long_sleep"
        }
      ],
      "next_token": null
    },
    "daily_readiness": {
      "data": [
        {
          "id": "dr-2026-05-12",
          "day": "2026-05-12",
          "score": 72,
          "temperature_deviation": 0.3
        }
      ],
      "next_token": null
    },
    "daily_activity": {
      "data": [
        {
          "id": "da-2026-05-12",
          "day": "2026-05-12",
          "score": 81
        }
      ],
      "next_token": null
    }
  }
}
```

- [ ] **Step 8.3: Write failing test `tests/test_sources_oura.py`**

```python
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from health_metrics.sources.oura import OuraClient


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "oura_responses.json"


@pytest.fixture
def oura_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.mark.asyncio
@respx.mock
async def test_oura_client_fetches_and_normalizes_single_date(oura_fixture):
    day = "2026-05-12"
    fx = oura_fixture[day]

    respx.get("https://api.ouraring.com/v2/usercollection/daily_sleep").mock(
        return_value=httpx.Response(200, json=fx["daily_sleep"])
    )
    respx.get("https://api.ouraring.com/v2/usercollection/sleep").mock(
        return_value=httpx.Response(200, json=fx["sleep"])
    )
    respx.get("https://api.ouraring.com/v2/usercollection/daily_readiness").mock(
        return_value=httpx.Response(200, json=fx["daily_readiness"])
    )
    respx.get("https://api.ouraring.com/v2/usercollection/daily_activity").mock(
        return_value=httpx.Response(200, json=fx["daily_activity"])
    )

    client = OuraClient(token="test-token")
    payload = await client.fetch_day(date.fromisoformat(day))
    await client.close()

    assert payload.metric_date == date(2026, 5, 12)
    assert payload.sleep_score == 78
    assert payload.sleep_duration_min == 24720 // 60          # = 412
    assert payload.sleep_efficiency == 89.2
    assert payload.sleep_latency_min == 540 // 60              # = 9
    assert payload.rem_min == 5040 // 60                       # = 84
    assert payload.deep_min == 4200 // 60                      # = 70
    assert payload.light_min == 15480 // 60                    # = 258
    assert payload.awake_min == 1080 // 60                     # = 18
    assert payload.hrv_avg == 45
    assert payload.rhr == 58
    assert payload.temp_deviation == 0.3
    assert payload.readiness_score == 72
```

- [ ] **Step 8.4: Run test → expect ImportError**

```bash
pytest tests/test_sources_oura.py -v
```

Expected: ImportError on `health_metrics.sources.oura`.

- [ ] **Step 8.5: Write `src/health_metrics/sources/oura.py`**

```python
"""Oura v2 client. Auth: Personal Access Token bearer header."""

from datetime import date
from typing import Any

import httpx
import structlog

from .base import OuraDayPayload

log = structlog.get_logger()


class OuraClient:
    def __init__(self, token: str, base_url: str = "https://api.ouraring.com/v2"):
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self._token}"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        resp = await self._http.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def fetch_day(self, day: date) -> OuraDayPayload:
        d = day.isoformat()
        params = {"start_date": d, "end_date": d}

        daily_sleep = await self._get("usercollection/daily_sleep", params)
        sleep = await self._get("usercollection/sleep", params)
        daily_readiness = await self._get("usercollection/daily_readiness", params)
        daily_activity = await self._get("usercollection/daily_activity", params)

        sleep_score = _first(daily_sleep, "score")
        readiness = _first(daily_readiness, "score")
        temp_dev = _first(daily_readiness, "temperature_deviation")

        primary_sleep = _longest_sleep_session(sleep.get("data", []))
        total_sec = (primary_sleep or {}).get("total_sleep_duration")
        awake_sec = (primary_sleep or {}).get("awake_time")
        rem_sec = (primary_sleep or {}).get("rem_sleep_duration")
        deep_sec = (primary_sleep or {}).get("deep_sleep_duration")
        light_sec = (primary_sleep or {}).get("light_sleep_duration")
        latency_sec = (primary_sleep or {}).get("latency")
        efficiency = (primary_sleep or {}).get("efficiency")
        hrv_avg = (primary_sleep or {}).get("average_hrv")
        rhr = (primary_sleep or {}).get("lowest_heart_rate")

        return OuraDayPayload(
            metric_date=day,
            sleep_score=sleep_score,
            sleep_duration_min=_sec_to_min(total_sec),
            sleep_efficiency=efficiency,
            sleep_latency_min=_sec_to_min(latency_sec),
            rem_min=_sec_to_min(rem_sec),
            deep_min=_sec_to_min(deep_sec),
            light_min=_sec_to_min(light_sec),
            awake_min=_sec_to_min(awake_sec),
            hrv_avg=hrv_avg,
            rhr=rhr,
            temp_deviation=temp_dev,
            readiness_score=readiness,
            raw={
                "daily_sleep": daily_sleep,
                "sleep": sleep,
                "daily_readiness": daily_readiness,
                "daily_activity": daily_activity,
            },
        )


def _first(envelope: dict[str, Any], key: str) -> Any:
    data = envelope.get("data") or []
    if not data:
        return None
    return data[0].get(key)


def _longest_sleep_session(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not sessions:
        return None
    return max(sessions, key=lambda s: s.get("total_sleep_duration") or 0)


def _sec_to_min(sec: int | None) -> int | None:
    if sec is None:
        return None
    return int(sec) // 60
```

- [ ] **Step 8.6: Run test → expect PASS**

```bash
pytest tests/test_sources_oura.py -v
```

Expected: 1 passed.

- [ ] **Step 8.7: Commit**

```bash
git add src/health_metrics/sources/ tests/fixtures/oura_responses.json tests/test_sources_oura.py
git commit -m "feat: Oura v2 client with fixture-based tests"
```

---

## Task 9: Whoop OAuth client + token rotation + fixtures + tests

**Files:**
- Create: `src/health_metrics/sources/whoop.py`
- Create: `scripts/whoop_oauth_bootstrap.py`
- Create: `tests/fixtures/whoop_responses.json`
- Create: `tests/test_sources_whoop.py`

- [ ] **Step 9.1: Write `tests/fixtures/whoop_responses.json`**

```json
{
  "2026-05-12": {
    "cycle": {
      "records": [
        {
          "id": 1234567,
          "user_id": 987654,
          "start": "2026-05-12T07:00:00.000Z",
          "end": "2026-05-13T07:00:00.000Z",
          "timezone_offset": "-04:00",
          "score_state": "SCORED",
          "score": {
            "strain": 14.2,
            "kilojoule": 9800.5,
            "average_heart_rate": 92,
            "max_heart_rate": 165
          }
        }
      ],
      "next_token": null
    },
    "recovery": {
      "records": [
        {
          "cycle_id": 1234567,
          "sleep_id": 7654321,
          "user_id": 987654,
          "created_at": "2026-05-12T07:30:00.000Z",
          "updated_at": "2026-05-12T07:30:00.000Z",
          "score_state": "SCORED",
          "score": {
            "user_calibrating": false,
            "recovery_score": 65,
            "resting_heart_rate": 60,
            "hrv_rmssd_milli": 42.5,
            "spo2_percentage": 97.0,
            "skin_temp_celsius": 33.4
          }
        }
      ],
      "next_token": null
    },
    "sleep": {
      "records": [
        {
          "id": 7654321,
          "user_id": 987654,
          "start": "2026-05-11T23:30:00.000Z",
          "end": "2026-05-12T07:15:00.000Z",
          "timezone_offset": "-04:00",
          "nap": false,
          "score_state": "SCORED",
          "score": {
            "stage_summary": {
              "total_in_bed_time_milli": 27900000,
              "total_awake_time_milli": 1080000,
              "total_no_data_time_milli": 0,
              "total_light_sleep_time_milli": 15480000,
              "total_slow_wave_sleep_time_milli": 4200000,
              "total_rem_sleep_time_milli": 5040000,
              "sleep_cycle_count": 5,
              "disturbance_count": 3
            },
            "sleep_needed": {
              "baseline_milli": 28800000,
              "need_from_sleep_debt_milli": 5400000,
              "need_from_recent_strain_milli": 1800000,
              "need_from_recent_nap_milli": 0
            },
            "respiratory_rate": 14.5,
            "sleep_performance_percentage": 82,
            "sleep_consistency_percentage": 78,
            "sleep_efficiency_percentage": 89
          }
        }
      ],
      "next_token": null
    },
    "workout": {
      "records": [
        {
          "id": "wkt-abc123",
          "user_id": 987654,
          "start": "2026-05-12T17:00:00.000Z",
          "end": "2026-05-12T17:45:00.000Z",
          "timezone_offset": "-04:00",
          "sport_id": 1,
          "score_state": "SCORED",
          "score": {
            "strain": 14.2,
            "average_heart_rate": 135,
            "max_heart_rate": 168,
            "kilojoule": 1620,
            "percent_recorded": 100,
            "distance_meter": 18500,
            "altitude_gain_meter": 120,
            "altitude_change_meter": 5,
            "zone_duration": {
              "zone_zero_milli": 60000,
              "zone_one_milli": 240000,
              "zone_two_milli": 720000,
              "zone_three_milli": 900000,
              "zone_four_milli": 660000,
              "zone_five_milli": 120000
            }
          }
        }
      ],
      "next_token": null
    }
  }
}
```

Note: `sport_id: 1` corresponds to "cycling" in Whoop's reference. The client maps this to `workout_type="cycling"` via a small lookup.

- [ ] **Step 9.2: Write failing test `tests/test_sources_whoop.py`**

```python
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from health_metrics.sources.whoop import WhoopClient


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "whoop_responses.json"


@pytest.fixture
def whoop_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.mark.asyncio
@respx.mock
async def test_whoop_client_fetches_and_normalizes(whoop_fixture):
    day = "2026-05-12"
    fx = whoop_fixture[day]

    respx.get("https://api.prod.whoop.com/developer/v1/cycle").mock(
        return_value=httpx.Response(200, json=fx["cycle"])
    )
    respx.get("https://api.prod.whoop.com/developer/v1/recovery").mock(
        return_value=httpx.Response(200, json=fx["recovery"])
    )
    respx.get("https://api.prod.whoop.com/developer/v1/activity/sleep").mock(
        return_value=httpx.Response(200, json=fx["sleep"])
    )
    respx.get("https://api.prod.whoop.com/developer/v1/activity/workout").mock(
        return_value=httpx.Response(200, json=fx["workout"])
    )

    client = WhoopClient(access_token="test-access", refresh_token="test-refresh",
                         client_id="cid", client_secret="csec")
    day_payload, workouts = await client.fetch_day(date.fromisoformat(day))
    await client.close()

    assert day_payload.recovery_score == 65
    assert day_payload.hrv_ms == 42.5
    assert day_payload.rhr == 60
    assert day_payload.sleep_performance == 82
    # 5,400,000 ms / 60000 = 90 min sleep debt
    assert day_payload.sleep_debt_min == 90
    # 28,800,000 ms / 60000 = 480 min sleep need baseline (spec field is need_from_baseline, but fixture has baseline)
    # Per spec: whoop_sleep_need_min ← sleep.score.sleep_needed.need_from_baseline_milli / 60000
    # We map from sleep_needed.baseline_milli (per actual API field name) — both are aliases in some docs
    assert day_payload.sleep_need_min == 480
    assert day_payload.day_strain == 14.2
    # kcal = 9800.5 kJ * 0.239 = 2342.32 → int 2342
    assert day_payload.kcal_burned == int(9800.5 * 0.239)
    assert day_payload.avg_hr == 92
    assert day_payload.max_hr == 165

    assert len(workouts) == 1
    w = workouts[0]
    assert w.source_id == "wkt-abc123"
    assert w.workout_date == date(2026, 5, 12)
    assert w.workout_type == "cycling"
    assert w.duration_min == 45
    assert w.strain == 14.2
    assert w.avg_hr == 135
    assert w.max_hr == 168
    # zone_zero_milli=60000 → 1 min
    assert w.zone_minutes[0] == 1
    assert w.zone_minutes[2] == 12
```

- [ ] **Step 9.3: Run test → expect ImportError**

```bash
pytest tests/test_sources_whoop.py -v
```

Expected: ImportError on `health_metrics.sources.whoop`.

- [ ] **Step 9.4: Write `src/health_metrics/sources/whoop.py`**

```python
"""Whoop developer v1 client with OAuth refresh-token rotation."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx
import structlog

from .base import WhoopDayPayload, WhoopWorkout

log = structlog.get_logger()


# Whoop sport_id → human label (small subset; expand as needed)
SPORT_ID_TO_TYPE: dict[int, str] = {
    -1: "activity",
    0: "running",
    1: "cycling",
    16: "baseball",
    17: "basketball",
    24: "golf",
    33: "hockey",
    42: "lacrosse",
    44: "rugby",
    45: "sailing",
    47: "skiing",
    48: "soccer",
    49: "softball",
    51: "squash",
    52: "swimming",
    53: "tennis",
    55: "volleyball",
    56: "water_polo",
    60: "yoga",
    61: "weightlifting",
    62: "crossfit",
    63: "functional_fitness",
    64: "pilates",
    65: "hiit",
    66: "spin",
    67: "stairs",
    68: "conditioning",
    69: "hiking",
    70: "rowing",
}


TokenRefreshCallback = Callable[[str, str, datetime], Awaitable[None]]


class WhoopClient:
    """
    Async Whoop client.

    On 401, transparently refreshes the access token using the refresh_token
    grant. Whoop rotates refresh tokens on every refresh — the optional
    `on_token_refresh` callback is invoked with (access_token, refresh_token,
    expires_at) so the caller can persist them.
    """

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        base_url: str = "https://api.prod.whoop.com/developer/v1",
        oauth_url: str = "https://api.prod.whoop.com/oauth/oauth2/token",
        on_token_refresh: TokenRefreshCallback | None = None,
    ):
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._oauth_url = oauth_url
        self._on_token_refresh = on_token_refresh
        self._http = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._http.aclose()

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    async def _refresh(self) -> None:
        """Exchange refresh_token for a new access_token (rotates refresh_token)."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": "offline",
        }
        resp = await self._http.post(self._oauth_url, data=data)
        resp.raise_for_status()
        body = resp.json()
        self._access_token = body["access_token"]
        self._refresh_token = body.get("refresh_token", self._refresh_token)
        expires_in = int(body.get("expires_in", 3600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        log.info("whoop_token_refreshed", expires_at=expires_at.isoformat())
        if self._on_token_refresh:
            await self._on_token_refresh(self._access_token, self._refresh_token, expires_at)

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        resp = await self._http.get(url, params=params, headers=headers)
        if resp.status_code == 401:
            await self._refresh()
            headers["Authorization"] = f"Bearer {self._access_token}"
            resp = await self._http.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def fetch_day(self, day: date) -> tuple[WhoopDayPayload, list[WhoopWorkout]]:
        start = f"{day.isoformat()}T00:00:00.000Z"
        end = f"{day.isoformat()}T23:59:59.999Z"
        params = {"start": start, "end": end}

        cycle = await self._get("cycle", params)
        recovery = await self._get("recovery", params)
        sleep = await self._get("activity/sleep", params)
        workout = await self._get("activity/workout", params)

        cycle_rec = _first_record(cycle)
        rec_rec = _first_record(recovery)
        sleep_rec = _first_record(sleep)

        cycle_score = (cycle_rec or {}).get("score") or {}
        rec_score = (rec_rec or {}).get("score") or {}
        sleep_score = (sleep_rec or {}).get("score") or {}
        sleep_needed = sleep_score.get("sleep_needed") or {}

        day_payload = WhoopDayPayload(
            metric_date=day,
            recovery_score=rec_score.get("recovery_score"),
            hrv_ms=rec_score.get("hrv_rmssd_milli"),
            rhr=rec_score.get("resting_heart_rate"),
            sleep_performance=sleep_score.get("sleep_performance_percentage"),
            sleep_need_min=_ms_to_min(sleep_needed.get("baseline_milli")),
            sleep_debt_min=_ms_to_min(sleep_needed.get("need_from_sleep_debt_milli")),
            day_strain=cycle_score.get("strain"),
            avg_hr=cycle_score.get("average_heart_rate"),
            max_hr=cycle_score.get("max_heart_rate"),
            kcal_burned=_kj_to_kcal(cycle_score.get("kilojoule")),
            raw={
                "cycle": cycle,
                "recovery": recovery,
                "sleep": sleep,
                "workout": workout,
            },
        )

        workouts: list[WhoopWorkout] = []
        for w in workout.get("records", []):
            workouts.append(_parse_workout(w, day))

        return day_payload, workouts


def _first_record(envelope: dict[str, Any]) -> dict[str, Any] | None:
    recs = envelope.get("records") or []
    if not recs:
        return None
    return recs[0]


def _ms_to_min(ms: int | float | None) -> int | None:
    if ms is None:
        return None
    return int(int(ms) / 60000)


def _kj_to_kcal(kj: float | int | None) -> int | None:
    if kj is None:
        return None
    return int(round(float(kj) * 0.239))


def _parse_workout(w: dict[str, Any], requested_day: date) -> WhoopWorkout:
    score = w.get("score") or {}
    zd = score.get("zone_duration") or {}
    zone_minutes = {
        0: _ms_to_min(zd.get("zone_zero_milli")) or 0,
        1: _ms_to_min(zd.get("zone_one_milli")) or 0,
        2: _ms_to_min(zd.get("zone_two_milli")) or 0,
        3: _ms_to_min(zd.get("zone_three_milli")) or 0,
        4: _ms_to_min(zd.get("zone_four_milli")) or 0,
        5: _ms_to_min(zd.get("zone_five_milli")) or 0,
    }
    start_iso = w["start"]
    end_iso = w["end"]
    start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    duration_min = int((end_dt - start_dt).total_seconds() / 60)
    workout_date = start_dt.date()
    sport_id = w.get("sport_id")
    workout_type = SPORT_ID_TO_TYPE.get(sport_id) if sport_id is not None else None
    return WhoopWorkout(
        source_id=str(w["id"]),
        workout_date=workout_date,
        workout_type=workout_type,
        started_at=start_iso,
        duration_min=duration_min,
        avg_hr=score.get("average_heart_rate"),
        max_hr=score.get("max_heart_rate"),
        strain=score.get("strain"),
        kcal=_kj_to_kcal(score.get("kilojoule")),
        zone_minutes=zone_minutes,
        raw=w,
    )
```

- [ ] **Step 9.5: Run test → expect PASS**

```bash
pytest tests/test_sources_whoop.py -v
```

Expected: 1 passed.

- [ ] **Step 9.6: Write `scripts/whoop_oauth_bootstrap.py`** (one-time, run with real creds later)

```python
"""
One-time Whoop OAuth bootstrap.

Run this once with WHOOP_CLIENT_ID + WHOOP_CLIENT_SECRET set. It prints
an authorization URL — open it in a browser, authorize, and paste the
redirected `code` query param back into this script. It will then
exchange the code for access + refresh tokens, and write the refresh
token to .env.

DO NOT run unless you've actually configured a Whoop developer app.
"""

import asyncio
import os
import sys
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ["WHOOP_CLIENT_ID"]
CLIENT_SECRET = os.environ["WHOOP_CLIENT_SECRET"]
REDIRECT_URI = os.environ.get(
    "WHOOP_REDIRECT_URI", "http://localhost:8000/whoop/callback"
)
AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = [
    "read:cycles",
    "read:recovery",
    "read:sleep",
    "read:workout",
    "read:profile",
    "offline",
]


async def main() -> int:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": "bootstrap",
    }
    print(f"Open this URL, authorize, then paste back the `code` query param:\n\n{AUTH_URL}?{urlencode(params)}\n")
    code = input("code: ").strip()

    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
        resp.raise_for_status()
        body = resp.json()

    print("\nSuccess. Add this to your .env:")
    print(f"WHOOP_REFRESH_TOKEN={body['refresh_token']}")
    print(f"\nAccess token (expires in {body.get('expires_in')}s): {body['access_token']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 9.7: Commit**

```bash
git add src/health_metrics/sources/whoop.py scripts/whoop_oauth_bootstrap.py tests/fixtures/whoop_responses.json tests/test_sources_whoop.py
git commit -m "feat: Whoop client with OAuth refresh + fixture tests + bootstrap script"
```

---

## Task 10: Transforms — normalize + zscore

**Files:**
- Create: `src/health_metrics/transforms/__init__.py` (empty)
- Create: `src/health_metrics/transforms/normalize.py`
- Create: `src/health_metrics/transforms/zscore.py`
- Create: `tests/test_transforms_normalize.py`
- Create: `tests/test_transforms_zscore.py`

- [ ] **Step 10.1: Write failing test `tests/test_transforms_zscore.py`**

```python
from health_metrics.transforms.zscore import compute_zscore


def test_zscore_returns_none_for_short_baseline():
    assert compute_zscore(50.0, [48.0, 49.0]) is None


def test_zscore_returns_zero_for_zero_variance():
    assert compute_zscore(50.0, [50.0] * 7) == 0.0


def test_zscore_computes_above_baseline():
    baseline = [48.0, 49.0, 50.0, 51.0, 52.0, 50.0, 49.0]
    z = compute_zscore(55.0, baseline)
    assert z is not None
    assert z > 1.0


def test_zscore_computes_below_baseline():
    baseline = [48.0, 49.0, 50.0, 51.0, 52.0, 50.0, 49.0]
    z = compute_zscore(45.0, baseline)
    assert z is not None
    assert z < -1.0
```

- [ ] **Step 10.2: Run test → expect ImportError**

```bash
pytest tests/test_transforms_zscore.py -v
```

Expected: ImportError.

- [ ] **Step 10.3: Write `src/health_metrics/transforms/zscore.py`**

```python
"""14-day rolling z-score computation."""

import statistics

MIN_BASELINE = 7  # need at least 7 values to compute meaningful z


def compute_zscore(value: float, baseline_values: list[float]) -> float | None:
    """
    Return the z-score of `value` against `baseline_values`.

    The caller is responsible for excluding `value` itself from the baseline
    list. Returns None if baseline is too small to be meaningful. Returns
    0.0 if baseline has zero variance (don't treat 0.0 as missing data).
    """
    if len(baseline_values) < MIN_BASELINE:
        return None
    mean = statistics.mean(baseline_values)
    std = statistics.stdev(baseline_values)
    if std == 0:
        return 0.0
    return (value - mean) / std
```

- [ ] **Step 10.4: Run test → expect PASS**

```bash
pytest tests/test_transforms_zscore.py -v
```

Expected: 4 passed.

- [ ] **Step 10.5: Write `tests/test_transforms_normalize.py`**

```python
from datetime import date

from health_metrics.sources.base import OuraDayPayload, WhoopDayPayload
from health_metrics.transforms.normalize import build_daily_metrics_row


def test_build_daily_metrics_row_merges_both_sources():
    oura = OuraDayPayload(
        metric_date=date(2026, 5, 12),
        sleep_score=78,
        sleep_duration_min=412,
        sleep_efficiency=89.2,
        hrv_avg=45,
        rhr=58,
        readiness_score=72,
        raw={"k": "v"},
    )
    whoop = WhoopDayPayload(
        metric_date=date(2026, 5, 12),
        recovery_score=65,
        hrv_ms=42.5,
        rhr=60,
        day_strain=14.2,
        kcal_burned=2342,
        raw={"k2": "v2"},
    )

    row = build_daily_metrics_row(
        user_id="hugo",
        oura=oura,
        whoop=whoop,
        oura_status="ok",
        whoop_status="ok",
    )

    assert row["user_id"] == "hugo"
    assert row["metric_date"] == date(2026, 5, 12)
    assert row["oura_sleep_score"] == 78
    assert row["oura_hrv_avg"] == 45
    assert row["whoop_recovery_score"] == 65
    assert row["whoop_hrv_ms"] == 42.5
    assert row["whoop_day_strain"] == 14.2
    assert row["whoop_kcal_burned"] == 2342
    assert row["ingestion_complete"] is True
    assert row["oura_status"] == "ok"
    assert row["whoop_status"] == "ok"


def test_build_daily_metrics_row_partial_oura_only():
    oura = OuraDayPayload(metric_date=date(2026, 5, 12), sleep_score=78)
    row = build_daily_metrics_row(
        user_id="hugo",
        oura=oura,
        whoop=None,
        oura_status="ok",
        whoop_status="failed",
    )
    assert row["oura_sleep_score"] == 78
    assert row["whoop_recovery_score"] is None
    assert row["ingestion_complete"] is False
```

- [ ] **Step 10.6: Write `src/health_metrics/transforms/normalize.py`**

```python
"""Build daily_metrics row dict from source payloads."""

from datetime import date
from typing import Any

from ..sources.base import OuraDayPayload, WhoopDayPayload


def build_daily_metrics_row(
    user_id: str,
    oura: OuraDayPayload | None,
    whoop: WhoopDayPayload | None,
    oura_status: str,
    whoop_status: str,
) -> dict[str, Any]:
    """Produce a dict matching the DailyMetrics ORM column names."""
    metric_date: date = (oura.metric_date if oura else whoop.metric_date)  # type: ignore[union-attr]

    row: dict[str, Any] = {
        "user_id": user_id,
        "metric_date": metric_date,
        # Oura
        "oura_sleep_score": _g(oura, "sleep_score"),
        "oura_sleep_duration_min": _g(oura, "sleep_duration_min"),
        "oura_sleep_efficiency": _g(oura, "sleep_efficiency"),
        "oura_sleep_latency_min": _g(oura, "sleep_latency_min"),
        "oura_rem_min": _g(oura, "rem_min"),
        "oura_deep_min": _g(oura, "deep_min"),
        "oura_light_min": _g(oura, "light_min"),
        "oura_awake_min": _g(oura, "awake_min"),
        "oura_hrv_avg": _g(oura, "hrv_avg"),
        "oura_rhr": _g(oura, "rhr"),
        "oura_temp_deviation": _g(oura, "temp_deviation"),
        "oura_readiness_score": _g(oura, "readiness_score"),
        "oura_raw": _g(oura, "raw"),
        # Whoop
        "whoop_recovery_score": _g(whoop, "recovery_score"),
        "whoop_hrv_ms": _g(whoop, "hrv_ms"),
        "whoop_rhr": _g(whoop, "rhr"),
        "whoop_sleep_performance": _g(whoop, "sleep_performance"),
        "whoop_sleep_need_min": _g(whoop, "sleep_need_min"),
        "whoop_sleep_debt_min": _g(whoop, "sleep_debt_min"),
        "whoop_day_strain": _g(whoop, "day_strain"),
        "whoop_avg_hr": _g(whoop, "avg_hr"),
        "whoop_max_hr": _g(whoop, "max_hr"),
        "whoop_kcal_burned": _g(whoop, "kcal_burned"),
        "whoop_raw": _g(whoop, "raw"),
        # Status
        "oura_status": oura_status,
        "whoop_status": whoop_status,
        "ingestion_complete": oura_status == "ok" and whoop_status == "ok",
    }
    return row


def _g(obj: Any, attr: str) -> Any:
    if obj is None:
        return None
    return getattr(obj, attr, None)
```

- [ ] **Step 10.7: Run test → expect PASS**

```bash
pytest tests/test_transforms_normalize.py -v
```

Expected: 2 passed.

- [ ] **Step 10.8: Commit**

```bash
git add src/health_metrics/transforms/ tests/test_transforms_normalize.py tests/test_transforms_zscore.py
git commit -m "feat: normalize + zscore transforms with tests"
```

---

## Task 11: Daily ingest job + integration test + manual /ingest/daily endpoint

**Files:**
- Create: `src/health_metrics/jobs/__init__.py` (empty)
- Create: `src/health_metrics/jobs/daily_ingest.py`
- Create: `src/health_metrics/routes/ingest.py`
- Modify: `src/health_metrics/main.py` (register ingest router)
- Test: `tests/test_jobs_daily_ingest.py`

- [ ] **Step 11.1: Write failing integration test `tests/test_jobs_daily_ingest.py`**

```python
import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from health_metrics.jobs.daily_ingest import run_daily_ingest
from health_metrics.models import DailyMetrics, Workout
from health_metrics.sources.base import OuraDayPayload, WhoopDayPayload, WhoopWorkout


FIXTURES = Path(__file__).parent / "fixtures"


def _oura_payload_from_fixture() -> OuraDayPayload:
    return OuraDayPayload(
        metric_date=date(2026, 5, 12),
        sleep_score=78,
        sleep_duration_min=412,
        sleep_efficiency=89.2,
        sleep_latency_min=9,
        rem_min=84,
        deep_min=70,
        light_min=258,
        awake_min=18,
        hrv_avg=45,
        rhr=58,
        temp_deviation=0.3,
        readiness_score=72,
        raw={},
    )


def _whoop_payload_and_workouts():
    payload = WhoopDayPayload(
        metric_date=date(2026, 5, 12),
        recovery_score=65,
        hrv_ms=42.5,
        rhr=60,
        sleep_performance=82,
        sleep_need_min=480,
        sleep_debt_min=90,
        day_strain=14.2,
        avg_hr=92,
        max_hr=165,
        kcal_burned=2342,
        raw={},
    )
    workouts = [
        WhoopWorkout(
            source_id="wkt-abc123",
            workout_date=date(2026, 5, 12),
            workout_type="cycling",
            started_at="2026-05-12T17:00:00.000Z",
            duration_min=45,
            avg_hr=135,
            max_hr=168,
            strain=14.2,
            kcal=387,
            zone_minutes={0: 1, 1: 4, 2: 12, 3: 15, 4: 11, 5: 2},
            raw={},
        )
    ]
    return payload, workouts


@pytest.mark.asyncio
async def test_single_date_ingest_writes_full_row(db_session):
    target_day = date(2026, 5, 12)

    oura_mock = AsyncMock()
    oura_mock.fetch_day.return_value = _oura_payload_from_fixture()
    oura_mock.close = AsyncMock()

    whoop_mock = AsyncMock()
    whoop_mock.fetch_day.return_value = _whoop_payload_and_workouts()
    whoop_mock.close = AsyncMock()

    with patch("health_metrics.jobs.daily_ingest._build_oura_client", return_value=oura_mock), \
         patch("health_metrics.jobs.daily_ingest._build_whoop_client", return_value=whoop_mock):
        await run_daily_ingest(day=target_day, user_id="hugo", session=db_session)

    # Assert daily_metrics row
    res = await db_session.execute(
        select(DailyMetrics).where(
            DailyMetrics.user_id == "hugo",
            DailyMetrics.metric_date == target_day,
        )
    )
    row = res.scalar_one()
    assert row.oura_sleep_score == 78
    assert row.oura_hrv_avg == 45
    assert row.whoop_recovery_score == 65
    assert float(row.whoop_hrv_ms) == 42.5
    assert float(row.whoop_day_strain) == 14.2
    assert row.whoop_kcal_burned == 2342
    assert row.ingestion_complete is True
    assert row.oura_status == "ok"
    assert row.whoop_status == "ok"
    # Single day → no baseline → z-scores are NULL
    assert row.unified_hrv_z is None
    assert row.unified_rhr_z is None
    assert row.unified_sleep_z is None

    # Assert workouts
    res = await db_session.execute(
        select(Workout).where(
            Workout.user_id == "hugo",
            Workout.workout_date == target_day,
        )
    )
    workouts = res.scalars().all()
    assert len(workouts) == 1
    w = workouts[0]
    assert w.source == "whoop"
    assert w.source_id == "wkt-abc123"
    assert w.workout_type == "cycling"
    assert w.duration_min == 45
    assert float(w.strain) == 14.2


@pytest.mark.asyncio
async def test_ingest_is_idempotent_for_same_date(db_session):
    """Running twice for the same date must not create duplicate rows."""
    target_day = date(2026, 5, 12)

    oura_mock = AsyncMock()
    oura_mock.fetch_day.return_value = _oura_payload_from_fixture()
    oura_mock.close = AsyncMock()

    whoop_payload, workouts = _whoop_payload_and_workouts()
    whoop_mock = AsyncMock()
    whoop_mock.fetch_day.return_value = (whoop_payload, workouts)
    whoop_mock.close = AsyncMock()

    with patch("health_metrics.jobs.daily_ingest._build_oura_client", return_value=oura_mock), \
         patch("health_metrics.jobs.daily_ingest._build_whoop_client", return_value=whoop_mock):
        await run_daily_ingest(day=target_day, user_id="hugo", session=db_session)
        await run_daily_ingest(day=target_day, user_id="hugo", session=db_session)

    res = await db_session.execute(
        select(DailyMetrics).where(
            DailyMetrics.user_id == "hugo",
            DailyMetrics.metric_date == target_day,
        )
    )
    rows = res.scalars().all()
    assert len(rows) == 1

    res = await db_session.execute(
        select(Workout).where(Workout.user_id == "hugo", Workout.workout_date == target_day)
    )
    workouts_db = res.scalars().all()
    assert len(workouts_db) == 1
```

- [ ] **Step 11.2: Run test → expect ImportError**

```bash
pytest tests/test_jobs_daily_ingest.py -v
```

Expected: ImportError on `health_metrics.jobs.daily_ingest`.

- [ ] **Step 11.3: Write `src/health_metrics/jobs/daily_ingest.py`**

```python
"""Daily ingest job — fetches Oura + Whoop for a single date and upserts to DB."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import DailyMetrics, Workout
from ..sources.oura import OuraClient
from ..sources.whoop import WhoopClient
from ..transforms.normalize import build_daily_metrics_row
from ..transforms.zscore import compute_zscore

log = structlog.get_logger()

ZSCORE_WINDOW_DAYS = 14


def _build_oura_client() -> OuraClient | None:
    settings = get_settings()
    if not settings.oura_personal_token:
        return None
    return OuraClient(token=settings.oura_personal_token, base_url=settings.oura_base_url)


def _build_whoop_client() -> WhoopClient | None:
    settings = get_settings()
    if not (settings.whoop_client_id and settings.whoop_client_secret and settings.whoop_refresh_token):
        return None
    return WhoopClient(
        access_token="",  # forces a refresh on first call
        refresh_token=settings.whoop_refresh_token,
        client_id=settings.whoop_client_id,
        client_secret=settings.whoop_client_secret,
        base_url=settings.whoop_base_url,
        oauth_url=settings.whoop_oauth_url,
    )


async def run_daily_ingest(day: date, user_id: str, session: AsyncSession) -> dict[str, Any]:
    """
    Pull Oura + Whoop for `day`, upsert to daily_metrics + workouts, recompute z-scores.

    Returns a small status dict for logging/HTTP responses.
    """
    oura_status = "skipped"
    whoop_status = "skipped"
    oura_payload = None
    whoop_payload = None
    whoop_workouts: list = []

    oura_client = _build_oura_client()
    if oura_client is not None:
        try:
            oura_payload = await oura_client.fetch_day(day)
            oura_status = "ok"
        except Exception as e:
            log.warning("oura_fetch_failed", day=day.isoformat(), error=str(e))
            oura_status = "failed"
        finally:
            await oura_client.close()

    whoop_client = _build_whoop_client()
    if whoop_client is not None:
        try:
            whoop_payload, whoop_workouts = await whoop_client.fetch_day(day)
            whoop_status = "ok"
        except Exception as e:
            log.warning("whoop_fetch_failed", day=day.isoformat(), error=str(e))
            whoop_status = "failed"
        finally:
            await whoop_client.close()

    row = build_daily_metrics_row(
        user_id=user_id,
        oura=oura_payload,
        whoop=whoop_payload,
        oura_status=oura_status,
        whoop_status=whoop_status,
    )

    # Upsert daily_metrics
    stmt = pg_insert(DailyMetrics).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "metric_date"],
        set_={k: v for k, v in row.items() if k not in ("user_id", "metric_date")},
    )
    await session.execute(stmt)

    # Upsert workouts (Whoop only for now)
    for w in whoop_workouts:
        w_row = {
            "user_id": user_id,
            "workout_date": w.workout_date,
            "source": "whoop",
            "source_id": w.source_id,
            "workout_type": w.workout_type,
            "started_at": w.started_at,
            "duration_min": w.duration_min,
            "avg_hr": w.avg_hr,
            "max_hr": w.max_hr,
            "strain": w.strain,
            "kcal": w.kcal,
            "zone_0_min": w.zone_minutes.get(0),
            "zone_1_min": w.zone_minutes.get(1),
            "zone_2_min": w.zone_minutes.get(2),
            "zone_3_min": w.zone_minutes.get(3),
            "zone_4_min": w.zone_minutes.get(4),
            "zone_5_min": w.zone_minutes.get(5),
            "raw": w.raw,
        }
        stmt = pg_insert(Workout).values(**w_row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_id"],
            set_={k: v for k, v in w_row.items() if k not in ("source", "source_id")},
        )
        await session.execute(stmt)

    # Recompute z-scores for the trailing window
    await _recompute_zscores(session, user_id=user_id, anchor_day=day)

    await session.commit()

    log.info(
        "daily_ingest_complete",
        day=day.isoformat(),
        user_id=user_id,
        oura_status=oura_status,
        whoop_status=whoop_status,
        workouts=len(whoop_workouts),
    )
    return {
        "day": day.isoformat(),
        "user_id": user_id,
        "oura_status": oura_status,
        "whoop_status": whoop_status,
        "workout_count": len(whoop_workouts),
    }


async def _recompute_zscores(session: AsyncSession, user_id: str, anchor_day: date) -> None:
    """
    For each day in [anchor - 13, anchor], recompute hrv/rhr/sleep z-scores
    against the trailing 14-day baseline (excluding the day being scored).

    With <7 baseline values, the z-score is NULL — that's the cold-start case.
    """
    window_start = anchor_day - timedelta(days=ZSCORE_WINDOW_DAYS - 1)
    res = await session.execute(
        select(DailyMetrics)
        .where(DailyMetrics.user_id == user_id)
        .where(DailyMetrics.metric_date >= window_start - timedelta(days=ZSCORE_WINDOW_DAYS))
        .where(DailyMetrics.metric_date <= anchor_day)
        .order_by(DailyMetrics.metric_date.asc())
    )
    all_rows = list(res.scalars().all())
    by_date = {r.metric_date: r for r in all_rows}

    for target_date, row in by_date.items():
        if target_date < window_start:
            continue
        baseline = [
            (r.oura_hrv_avg or r.whoop_hrv_ms)
            for r in all_rows
            if r.metric_date < target_date
            and r.metric_date >= target_date - timedelta(days=ZSCORE_WINDOW_DAYS)
            and (r.oura_hrv_avg is not None or r.whoop_hrv_ms is not None)
        ]
        baseline_hrv = [float(b) for b in baseline if b is not None]
        cur_hrv = float(row.oura_hrv_avg) if row.oura_hrv_avg is not None else (
            float(row.whoop_hrv_ms) if row.whoop_hrv_ms is not None else None
        )
        if cur_hrv is not None:
            z = compute_zscore(cur_hrv, baseline_hrv)
            row.unified_hrv_z = Decimal(f"{z:.2f}") if z is not None else None

        baseline_rhr = [
            float(r.oura_rhr) if r.oura_rhr is not None else float(r.whoop_rhr)
            for r in all_rows
            if r.metric_date < target_date
            and r.metric_date >= target_date - timedelta(days=ZSCORE_WINDOW_DAYS)
            and (r.oura_rhr is not None or r.whoop_rhr is not None)
        ]
        cur_rhr = float(row.oura_rhr) if row.oura_rhr is not None else (
            float(row.whoop_rhr) if row.whoop_rhr is not None else None
        )
        if cur_rhr is not None:
            z = compute_zscore(cur_rhr, baseline_rhr)
            row.unified_rhr_z = Decimal(f"{z:.2f}") if z is not None else None

        baseline_sleep = [
            float(r.oura_sleep_duration_min)
            for r in all_rows
            if r.metric_date < target_date
            and r.metric_date >= target_date - timedelta(days=ZSCORE_WINDOW_DAYS)
            and r.oura_sleep_duration_min is not None
        ]
        cur_sleep = float(row.oura_sleep_duration_min) if row.oura_sleep_duration_min is not None else None
        if cur_sleep is not None:
            z = compute_zscore(cur_sleep, baseline_sleep)
            row.unified_sleep_z = Decimal(f"{z:.2f}") if z is not None else None
```

- [ ] **Step 11.4: Run test → expect PASS**

```bash
pytest tests/test_jobs_daily_ingest.py -v
```

Expected: 2 passed.

- [ ] **Step 11.5: Write `src/health_metrics/routes/ingest.py`**

```python
"""Manual ingest trigger — POST /ingest/daily?date=YYYY-MM-DD"""

from datetime import date as date_type, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, HTTPException, Query

from ..config import get_settings
from ..db import AsyncSessionLocal
from ..jobs.daily_ingest import run_daily_ingest

log = structlog.get_logger()
router = APIRouter()


@router.post("/ingest/daily")
async def trigger_daily_ingest(
    date: str | None = Query(default=None, description="YYYY-MM-DD; default = yesterday in service TZ"),
    user_id: str | None = Query(default=None),
):
    settings = get_settings()
    target_user = user_id or settings.user_id

    if date is None:
        tz = ZoneInfo(settings.timezone)
        target_day = datetime.now(tz).date() - timedelta(days=1)
    else:
        try:
            target_day = date_type.fromisoformat(date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"invalid date: {e}")

    async with AsyncSessionLocal() as session:
        result = await run_daily_ingest(day=target_day, user_id=target_user, session=session)
    return result
```

- [ ] **Step 11.6: Wire ingest router into `src/health_metrics/main.py`**

Modify `main.py`:

```python
# In imports block:
from .routes import health as health_route, ingest as ingest_route  # noqa: E402

# After app.include_router(health_route.router):
app.include_router(ingest_route.router)
```

- [ ] **Step 11.7: Run full test suite**

```bash
pytest -v
```

Expected: All tests pass.

- [ ] **Step 11.8: Manual smoke verification against running app**

```bash
# Make sure DB is up and migrated
docker compose up -d postgres
alembic upgrade head

# Pre-clear any prior row for 2026-05-12 + hugo
docker exec hms-postgres psql -U hms -d health_metrics -c \
  "DELETE FROM daily_metrics WHERE user_id='hugo' AND metric_date='2026-05-12'; DELETE FROM workouts WHERE user_id='hugo' AND workout_date='2026-05-12';"

# Boot the app
uvicorn src.health_metrics.main:app --port 8000 &
UV_PID=$!
sleep 2

# Trigger
curl -s -X POST 'http://localhost:8000/ingest/daily?date=2026-05-12&user_id=hugo'
echo

# Inspect — note: with no real tokens, oura/whoop status will be "skipped"
docker exec hms-postgres psql -U hms -d health_metrics -c \
  "SELECT user_id, metric_date, oura_status, whoop_status, ingestion_complete FROM daily_metrics WHERE metric_date='2026-05-12';"

kill $UV_PID
```

Expected (no live tokens — fixtures-only phase): JSON response with `oura_status: "skipped"`, `whoop_status: "skipped"`, `workout_count: 0`. A daily_metrics row exists for `2026-05-12` with both statuses = `skipped` and `ingestion_complete = false`.

This proves the wiring works end-to-end. Real ingest will replace `skipped` with `ok` once tokens are wired up in Phase 2.

- [ ] **Step 11.9: Commit**

```bash
git add src/health_metrics/jobs/ src/health_metrics/routes/ingest.py src/health_metrics/main.py tests/test_jobs_daily_ingest.py
git commit -m "feat: daily ingest job + POST /ingest/daily endpoint + integration test"
```

---

## Task 12: Dockerfile + railway.toml + final phase-1 commit

**Files:**
- Create: `Dockerfile`
- Create: `Procfile`
- Create: `railway.toml`

These are committed for Phase 2 deployment but not exercised in Phase 1.

- [ ] **Step 12.1: Write `Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "src.health_metrics.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 12.2: Write `Procfile`**

```
web: uvicorn src.health_metrics.main:app --host 0.0.0.0 --port $PORT
release: alembic upgrade head
```

- [ ] **Step 12.3: Write `railway.toml`**

```toml
[build]
builder = "DOCKERFILE"

[deploy]
startCommand = "uvicorn src.health_metrics.main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

- [ ] **Step 12.4: Commit**

```bash
git add Dockerfile Procfile railway.toml
git commit -m "chore: Dockerfile + railway.toml for Phase 2 deploy (unrun in Phase 1)"
```

- [ ] **Step 12.5: Final phase-1 tag**

```bash
git tag -a v0.1.0-phase1 -m "Phase 1 (steps 1-5) complete: scaffold + Oura + Whoop + daily ingest verified for single date"
git log --oneline | head -20
```

Expected: clean linear history, phase-1 tag at HEAD.

---

## Validation checklist (Phase 1 exit criteria)

- [ ] `pytest -v` — all tests pass
- [ ] `docker compose ps` — postgres healthy
- [ ] `alembic upgrade head` — succeeds, schema matches spec §3
- [ ] `psql \dt` — shows all 5 tables + `alembic_version`
- [ ] `tests/test_jobs_daily_ingest.py::test_single_date_ingest_writes_full_row` — passes
- [ ] `tests/test_jobs_daily_ingest.py::test_ingest_is_idempotent_for_same_date` — passes
- [ ] Manual `curl -X POST /ingest/daily?date=2026-05-12` against the running app returns 200 with the expected JSON
- [ ] Without live tokens: `oura_status` and `whoop_status` come back as `skipped` (graceful no-op, not a crash)
- [ ] `scripts/whoop_oauth_bootstrap.py` exists, imports clean, but is not run
- [ ] Git history is clean and each task is a single commit
- [ ] Phase-1 tag `v0.1.0-phase1` exists

## Deferred to Phase 2 (next session)

- Spec §11 step 6: `POST /backfill?days=30`
- Spec §11 step 7: APScheduler daily + retry jobs + Whoop token-refresh job
- Spec §11 steps 8-10: `mcp-unified-server/tools/health/` — 8 MCP tools
- Live Oura/Whoop credentials wired
- Railway provisioning + first deploy
- Conservative-bias and cold-start regression tests for the regulation logic
