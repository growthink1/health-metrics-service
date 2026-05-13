# Health Metrics Pipeline — Claude Code Handoff Spec

**Repo target**: new repo `growthink1/health-metrics-service` + additions to existing `growthink1/mcp-unified-server`
**Deployment**: Railway (alongside mcp-unified-server)
**Owner**: Hugo (single-user system, multi-user-ready schema)
**Purpose**: Ingest Oura + Whoop data daily, store in Postgres, expose MCP tools so Claude can pull recovery/strain state during planning sessions and execute auto-regulated training/nutrition recommendations.

---

## Context for Claude Code

Read these before starting:
- `growthink1/mcp-unified-server` — match its conventions exactly (FastMCP tool registration, structlog-to-stderr logging, async SQLAlchemy patterns, Railway deployment config, error envelope shape)
- `growthink1/hugo-claude-memory/CLAUDE_MEMORY.md` — additional context on infra patterns

The goal is two services:

1. **`health-metrics-service`** (new repo, FastAPI + APScheduler) — daily ingestion from Oura/Whoop APIs to Postgres
2. **`mcp-unified-server`** additions — new `tools/health/` module with 8 MCP tools that query Postgres

Both deployed to Railway, sharing a Postgres instance.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Railway                                  │
│                                                                  │
│   ┌──────────────────────┐    ┌─────────────────────────────┐   │
│   │ health-metrics-      │    │ mcp-unified-server          │   │
│   │  service             │    │  (existing)                 │   │
│   │                      │    │                             │   │
│   │ • FastAPI            │    │ • Existing 70+ tools        │   │
│   │ • APScheduler        │    │ • NEW: tools/health/        │   │
│   │ • Oura/Whoop ingest  │    │   - get_session_brief       │   │
│   │ • Z-score compute    │    │   - get_daily_snapshot      │   │
│   │ • Backfill workflow  │    │   - get_recovery_trend      │   │
│   │                      │    │   - get_strain_trend        │   │
│   │ Writes ──────────►   │    │   - get_auto_regulation_... │   │
│   │                      │    │   - get_workout_history     │   │
│   └──────────────────────┘    │   - get_weight_trend        │   │
│              │                 │   - log_manual_entry        │   │
│              ▼                 │                             │   │
│        ┌──────────┐            │   ◄────── Reads             │   │
│        │ Postgres │ ◄──────────┤                             │   │
│        │ (shared) │            │                             │   │
│        └──────────┘            └─────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

Why two services rather than one:
- mcp-unified-server is already deployed and stable — don't risk it with scheduled-job complexity
- Ingestion has different reliability/restart semantics (APScheduler state, OAuth token refresh) than the MCP server
- Clean separation of concerns; easier to test each in isolation

---

## Repo plan

### New repo: `growthink1/health-metrics-service`

```
health-metrics-service/
├── pyproject.toml
├── README.md
├── Dockerfile
├── railway.toml
├── .env.example
├── alembic.ini
├── alembic/
│   └── versions/
├── src/
│   └── health_metrics/
│       ├── __init__.py
│       ├── main.py              # FastAPI app entry
│       ├── config.py            # Pydantic Settings
│       ├── db.py                # Async SQLAlchemy engine + session
│       ├── models.py            # SQLAlchemy ORM models
│       ├── schemas.py           # Pydantic response/request schemas
│       ├── scheduler.py         # APScheduler setup
│       ├── routes/
│       │   ├── health.py        # /health endpoint
│       │   ├── ingest.py        # manual ingest trigger endpoints
│       │   └── backfill.py      # /backfill?days=30 endpoint
│       ├── sources/
│       │   ├── __init__.py
│       │   ├── base.py          # Abstract source interface
│       │   ├── oura.py          # OuraClient + ingestion logic
│       │   └── whoop.py         # WhoopClient + OAuth + ingestion
│       ├── transforms/
│       │   ├── normalize.py     # API payload → ORM
│       │   └── zscore.py        # 14d rolling z-score computation
│       └── jobs/
│           ├── daily_ingest.py  # scheduled daily pull
│           └── backfill.py      # historical backfill workflow
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   ├── oura_responses.json
    │   └── whoop_responses.json
    ├── test_sources_oura.py
    ├── test_sources_whoop.py
    ├── test_transforms_zscore.py
    └── test_jobs_daily_ingest.py
```

### Additions to `mcp-unified-server`

```
mcp-unified-server/
├── ... (existing)
└── tools/
    └── health/
        ├── __init__.py          # Tool registration
        ├── db.py                # Async SQLAlchemy read-only session
        ├── queries.py           # SQL queries / SQLAlchemy selects
        ├── regulation.py        # Auto-regulation decision tree
        ├── schemas.py           # Pydantic response models
        └── tools.py             # FastMCP @mcp.tool definitions
```

The `tools/health/db.py` should use a separate DB session config from any future write paths — read-only access only from the MCP server.

---

## Postgres schema

Create as Alembic migration in `health-metrics-service`. The MCP server reads but does not migrate.

```sql
-- Daily recovery + strain snapshot
CREATE TABLE daily_metrics (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    metric_date DATE NOT NULL,

    -- Oura: recovery truth source
    oura_sleep_score INTEGER,
    oura_sleep_duration_min INTEGER,
    oura_sleep_efficiency NUMERIC(5,2),
    oura_sleep_latency_min INTEGER,
    oura_rem_min INTEGER,
    oura_deep_min INTEGER,
    oura_light_min INTEGER,
    oura_awake_min INTEGER,
    oura_hrv_avg INTEGER,                     -- rMSSD ms
    oura_rhr INTEGER,
    oura_temp_deviation NUMERIC(4,2),         -- °C
    oura_readiness_score INTEGER,
    oura_raw JSONB,

    -- Whoop: strain truth source + secondary recovery
    whoop_recovery_score INTEGER,
    whoop_hrv_ms NUMERIC(6,2),
    whoop_rhr INTEGER,
    whoop_sleep_performance INTEGER,
    whoop_sleep_need_min INTEGER,
    whoop_sleep_debt_min INTEGER,
    whoop_day_strain NUMERIC(4,2),
    whoop_avg_hr INTEGER,
    whoop_max_hr INTEGER,
    whoop_kcal_burned INTEGER,
    whoop_raw JSONB,

    -- Derived
    unified_hrv_z NUMERIC(5,2),
    unified_rhr_z NUMERIC(5,2),
    unified_sleep_z NUMERIC(5,2),

    ingestion_complete BOOLEAN DEFAULT FALSE,
    oura_status TEXT,                         -- 'ok'|'partial'|'failed'|'skipped'
    whoop_status TEXT,
    ingested_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, metric_date)
);
CREATE INDEX idx_daily_metrics_user_date ON daily_metrics(user_id, metric_date DESC);

-- Per-session workouts
CREATE TABLE workouts (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    workout_date DATE NOT NULL,
    source TEXT NOT NULL,                     -- 'whoop'|'oura'|'manual'
    source_id TEXT NOT NULL,
    workout_type TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    duration_min INTEGER NOT NULL,
    avg_hr INTEGER,
    max_hr INTEGER,
    strain NUMERIC(4,2),
    kcal INTEGER,
    zone_0_min INTEGER, zone_1_min INTEGER, zone_2_min INTEGER,
    zone_3_min INTEGER, zone_4_min INTEGER, zone_5_min INTEGER,
    raw JSONB,
    UNIQUE(source, source_id)
);
CREATE INDEX idx_workouts_user_date ON workouts(user_id, workout_date DESC);
CREATE INDEX idx_workouts_type ON workouts(workout_type);

-- Manual log
CREATE TABLE manual_log (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    log_date DATE NOT NULL,
    weight_lbs NUMERIC(5,2),
    kcal_consumed INTEGER,
    protein_g INTEGER, fat_g INTEGER, carbs_g INTEGER,
    subjective_energy INTEGER,                -- 1-10
    subjective_mood INTEGER,
    subjective_hunger INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, log_date)
);

-- Auto-regulation audit trail
CREATE TABLE regulation_recommendations (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    rec_date DATE NOT NULL,
    rec_type TEXT,                            -- deficit|deficit_conservative|maintenance|deload
    suggested_kcal INTEGER,
    suggested_training_mod TEXT,
    confidence TEXT,                          -- high|medium|low
    rationale TEXT,
    triggering_signals JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_reg_rec_user_date ON regulation_recommendations(user_id, rec_date DESC);
```

---

## API integrations

### Oura v2

- Base URL: `https://api.ouraring.com/v2`
- Auth: Personal Access Token (header `Authorization: Bearer <token>`). Token via env var `OURA_PERSONAL_TOKEN`.
- Rate limit: 5000 req / 5 min — generous, not a concern
- Endpoints to consume:
  - `GET /usercollection/daily_sleep?start_date={d}&end_date={d}` → sleep score + contributors
  - `GET /usercollection/sleep?start_date={d}&end_date={d}` → detailed sleep sessions (HRV, latency, stages)
  - `GET /usercollection/daily_readiness?start_date={d}&end_date={d}` → readiness score + temp deviation
  - `GET /usercollection/daily_activity?start_date={d}&end_date={d}` → activity score (mostly for completeness)
- Date range: each endpoint supports up to 30-day range per request

**Mapping to `daily_metrics`** (see `transforms/normalize.py`):
- `oura_sleep_score` ← `daily_sleep.score`
- `oura_sleep_duration_min` ← `sleep[0].total_sleep_duration / 60`
- `oura_sleep_efficiency` ← `sleep[0].efficiency`
- `oura_hrv_avg` ← `sleep[0].average_hrv`
- `oura_rhr` ← `sleep[0].lowest_heart_rate`
- `oura_temp_deviation` ← `daily_readiness.temperature_deviation`
- `oura_readiness_score` ← `daily_readiness.score`

If date has multiple sleep sessions (rare but possible), take the longest session as primary.

### Whoop v1 (developer API)

- Base URL: `https://api.prod.whoop.com/developer/v1`
- Auth: **OAuth2 authorization code flow** — this is the harder integration
  - One-time: generate authorization URL, Hugo authorizes, exchange code for refresh+access tokens
  - Store `WHOOP_REFRESH_TOKEN` in env / Railway secrets
  - On each request: use access token; if 401, refresh via `POST /oauth/oauth2/token` with refresh_token grant
  - Implement token refresh as a context manager / decorator in `sources/whoop.py`
- Rate limit: 100 req/min — pace ingestion
- Endpoints:
  - `GET /cycle?start={iso}&end={iso}` → daily physiological cycle (paginated, 25/page)
  - `GET /recovery?start={iso}&end={iso}` → recovery score per cycle
  - `GET /sleep?start={iso}&end={iso}` → sleep sessions
  - `GET /workout?start={iso}&end={iso}` → workouts (this is the cycling source of truth)

**Mapping to `daily_metrics`**:
- `whoop_recovery_score` ← `recovery.score.recovery_score`
- `whoop_hrv_ms` ← `recovery.score.hrv_rmssd_milli`
- `whoop_rhr` ← `recovery.score.resting_heart_rate`
- `whoop_sleep_performance` ← `sleep.score.sleep_performance_percentage`
- `whoop_sleep_need_min` ← `sleep.score.sleep_needed.need_from_baseline_milli / 60000`
- `whoop_sleep_debt_min` ← `sleep.score.sleep_needed.need_from_sleep_debt_milli / 60000`
- `whoop_day_strain` ← `cycle.score.strain`
- `whoop_kcal_burned` ← `cycle.score.kilojoule * 0.239` (kJ → kcal)

**Mapping to `workouts`**: every `workout` API item becomes a row, deduped by `source_id`.

### Backfill workflow (Phase 1 — cold-start)

Per decision: backfill 30 days on first run.

`POST /backfill?days=30` endpoint in health-metrics-service:
1. For each of last 30 days, call `daily_ingest` job idempotently
2. Recompute z-scores across the full window once all days are loaded
3. Mark backfill_complete in a metadata table (or env flag)

Run this once after deploy, before MCP tools start serving traffic.

---

## MCP tools (8)

All tools live in `mcp-unified-server/tools/health/tools.py`, registered via FastMCP. All take `user_id: str = "hugo"` as final param. All return Pydantic models serialized to dict.

### 1. `get_session_brief()`

The tool Claude calls at the start of every planning session.

```python
{
    "as_of": "2026-05-13T14:30:00Z",
    "latest_metric_date": "2026-05-13",
    "data_freshness_hours": 8,
    "regulation": {
        # full get_auto_regulation_status payload nested
    },
    "weight_trend": {
        "current_lbs": 218.4,
        "7d_avg_lbs": 219.1,
        "weekly_change_lbs": -0.8,
        "on_target": True   # true if losing 0.75-1.5 lb/week
    },
    "recent_workouts": [
        {"date": "2026-05-12", "type": "cycling", "duration_min": 45, "strain": 14.2},
        {"date": "2026-05-11", "type": "strength", "duration_min": 60, "strain": 11.8}
    ],
    "missing_inputs": {
        "today_subjective_log": True,        # required, blocks high confidence
        "today_weight": False,
        "today_nutrition": True
    },
    "action_required": [
        "Log today's subjective markers (energy, mood, hunger)",
        "Log today's nutrition"
    ]
}
```

### 2. `get_daily_snapshot(date: str = "today")`

Single-day state.

```python
{
    "date": "2026-05-13",
    "recovery": {
        "oura_readiness": 78,
        "whoop_recovery": 65,
        "sleep_min": 412,
        "sleep_efficiency": 89.2,
        "hrv_ms": 45,
        "hrv_z_14d": -1.2,
        "rhr": 58,
        "rhr_z_14d": 1.4,
        "temp_deviation_c": 0.3
    },
    "strain": {
        "whoop_day_strain": 14.2,
        "workout_count": 1,
        "workout_minutes": 45,
        "primary_workout": "cycling"
    },
    "manual": {
        "weight_lbs": 218.4,
        "kcal_consumed": 2290,
        "protein_g": 195,
        "subjective_energy": 6,
        "subjective_mood": 7,
        "subjective_hunger": 5
    },
    "alerts": ["HRV depressed >1 SD vs 14d baseline"]
}
```

### 3. `get_recovery_trend(n_days: int = 14)`

```python
{
    "n_days": 14,
    "daily": [
        {"date": "2026-04-30", "hrv": 52, "rhr": 55, "sleep_min": 432, ...},
        # ...
    ],
    "rolling_stats": {
        "hrv": {"mean": 48.2, "std": 5.1, "trend_slope": -0.2},
        "rhr": {"mean": 56.4, "std": 2.3, "trend_slope": 0.3},
        "sleep_min": {"mean": 398, "std": 42, "trend_slope": -3.1}
    }
}
```

Trend slope = simple linear regression coefficient over the window. Negative HRV slope + positive RHR slope = trending toward overreaching.

### 4. `get_strain_trend(n_days: int = 14)`

```python
{
    "n_days": 14,
    "daily_strain": [
        {"date": "2026-04-30", "strain": 12.3, "workout_count": 1, "primary_type": "strength"},
        # ...
    ],
    "rolling": {
        "strain_7d_total": 78.4,
        "strain_7d_avg": 11.2,
        "strain_14d_avg": 10.8,
        "high_strain_days_7d": 3              # count of strain > 14
    },
    "by_type_14d": {
        "cycling": {"sessions": 5, "avg_strain": 13.1, "avg_duration_min": 42},
        "strength": {"sessions": 6, "avg_strain": 10.8, "avg_duration_min": 58}
    }
}
```

### 5. `get_auto_regulation_status()`

The decision call. Returns recommendation + rationale + raw signals.

```python
{
    "recommendation": "maintenance",
    "confidence": "high",                     # high|medium|low
    "rationale": [
        "Sleep 3d avg: 5.4h (target ≥7h)",
        "HRV depressed 1.4 SD over 3 days",
        "Cumulative 7d strain: 78 (high)"
    ],
    "suggested_kcal": 2800,
    "suggested_training_mod": "Reduce volume 20%, swap HIIT for Z2 today",
    "triggering_signals": {
        "hrv_z_3d_avg": -1.4,
        "rhr_z_3d_avg": 1.1,
        "sleep_3d_avg_min": 326,
        "sleep_debt_min": 540,
        "strain_7d_total": 78,
        "subjective_3d_avg": {"energy": 4.3, "mood": 5.0, "hunger": 7.7}
    },
    "computed_at": "2026-05-13T14:30:00Z"
}
```

**Confidence rules**:
- `high` — all 3 most recent days have `ingestion_complete = TRUE` AND today's subjective log exists
- `medium` — 2 of 3 days complete, or today's subjective log missing but yesterday's exists
- `low` — partial data OR cold-start period (<14 days of history). Recommendation still returned but flagged.

Also writes the recommendation to `regulation_recommendations` for audit.

### 6. `get_workout_history(start_date: str, end_date: str, workout_type: str = None)`

Returns list of workouts from `workouts` table. Use for "show me my last 30 days of cycling" type queries.

### 7. `get_weight_trend(n_days: int = 30)`

```python
{
    "n_days": 30,
    "current_lbs": 218.4,
    "start_lbs": 220.0,
    "total_change_lbs": -1.6,
    "weekly_rate_lbs": -0.8,
    "7d_moving_avg": [...],                   # daily array
    "raw": [...],
    "tdee_estimate_revised": 2890,            # back-calculated from intake vs loss
    "on_target": True,                        # 0.75 <= weekly_rate <= 1.5
    "kcal_adjustment_suggestion": null        # or "+200" / "-200" if off-target
}
```

The TDEE back-calculation is important: if his logged intake averages 2300 kcal and he's losing 0.5 lb/week (= 250 kcal/day deficit), his actual TDEE is 2550, not 2800. This tool surfaces that drift.

### 8. `log_manual_entry(date, weight_lbs?, kcal?, protein_g?, fat_g?, carbs_g?, energy?, mood?, hunger?, notes?)`

UPSERT semantics — accepts partial fields, merges with existing row. Returns:

```python
{
    "logged_date": "2026-05-13",
    "fields_updated": ["weight_lbs", "kcal_consumed", "protein_g"],
    "completeness": {
        "weight": True,
        "nutrition": True,
        "subjective": False                   # still missing — required
    },
    "next_required_inputs": ["subjective_energy", "subjective_mood", "subjective_hunger"]
}
```

---

## Auto-regulation logic

Implement in `tools/health/regulation.py`. Conservative bias confirmed.

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class RegulationSignals:
    hrv_z_3d: float            # avg z-score over last 3 days vs 14d baseline
    rhr_z_3d: float
    sleep_3d_min: float        # avg minutes over last 3 days
    sleep_debt_min: float      # from Whoop
    strain_7d_total: float
    subjective_3d_energy: float | None    # 1-10, None if missing
    days_with_complete_data: int          # of last 3, how many ingestion_complete

RecType = Literal["deficit", "deficit_conservative", "maintenance", "deload"]

def regulate(s: RegulationSignals) -> tuple[RecType, list[str], dict]:
    """
    Returns (recommendation, rationale_list, action_payload).
    Conservative bias: when ambiguous, prefer the safer recommendation.
    """
    rationale = []

    # Composite recovery score: lower = worse
    recovery_score = (
        -0.50 * s.hrv_z_3d                    # depressed HRV is bad
        -0.30 * s.rhr_z_3d                    # elevated RHR is bad
        + 0.40 * ((s.sleep_3d_min - 360) / 60)  # >6h positive
    )

    # Hard floor: severe sleep deprivation
    if s.sleep_3d_min < 300:
        rationale.append(f"Severe sleep debt: {s.sleep_3d_min/60:.1f}h avg over 3d")
        return ("deload", rationale, {"kcal": 2800, "training": "Volume -30%, Z2 only, extra rest day"})

    # Hard floor: subjective collapse (if logged)
    if s.subjective_3d_energy is not None and s.subjective_3d_energy < 4:
        rationale.append(f"Subjective energy collapsed: {s.subjective_3d_energy:.1f}/10")
        return ("deload", rationale, {"kcal": 2800, "training": "Volume -30%, Z2 only, extra rest day"})

    # Severe recovery + sleep compromise
    if recovery_score < -1.0 and s.sleep_3d_min < 360:
        rationale.append(f"Recovery composite {recovery_score:.2f} + sleep {s.sleep_3d_min/60:.1f}h")
        return ("deload", rationale, {"kcal": 2800, "training": "Volume -30%, swap HIIT for Z2"})

    # Mild recovery compromise — pause deficit, train normally
    if recovery_score < -0.5 or s.sleep_3d_min < 390:
        rationale.append(f"Recovery markers depressed (score {recovery_score:.2f}, sleep {s.sleep_3d_min/60:.1f}h)")
        return ("maintenance", rationale, {"kcal": 2800, "training": "Full program, no progression push"})

    # Excessive strain accumulation
    if s.strain_7d_total / 7 > 15:
        rationale.append(f"7d strain load high: {s.strain_7d_total:.1f} ({s.strain_7d_total/7:.1f}/day avg)")
        return ("deficit_conservative", rationale, {"kcal": 2500, "training": "Full program, monitor closely"})

    # All clear
    if recovery_score > 0 and s.strain_7d_total / 7 < 13:
        rationale.append(f"All signals green: recovery {recovery_score:.2f}, strain {s.strain_7d_total/7:.1f}/d")
        return ("deficit", rationale, {"kcal": 2300, "training": "Full program, progression OK"})

    # Conservative bias default
    rationale.append(f"Mixed signals (recovery {recovery_score:.2f}, strain {s.strain_7d_total/7:.1f}/d) — conservative")
    return ("deficit_conservative", rationale, {"kcal": 2500, "training": "Full program, monitor closely"})
```

Z-score computation (in `health-metrics-service/transforms/zscore.py`):

```python
def compute_zscore(value: float, baseline_values: list[float]) -> float | None:
    """14-day rolling z-score. Excludes value being scored from baseline."""
    if len(baseline_values) < 7:                # need minimum sample
        return None
    mean = statistics.mean(baseline_values)
    std = statistics.stdev(baseline_values)
    if std == 0:
        return 0.0
    return (value - mean) / std
```

Recompute z-scores for the last 14 days after every daily ingest (cheap operation, <100 rows).

---

## Subjective markers — required handling

Per decision: subjective markers required. Implementation:

1. `log_manual_entry` accepts subjective fields as optional in the API (so morning weight-only entries work)
2. `get_session_brief` checks for today's subjective log and includes `today_subjective_log: true` in `missing_inputs` if absent
3. `get_auto_regulation_status` returns `confidence: "medium"` (not "high") if today's subjective markers are missing, even if all device data is complete
4. The `action_required` array in `get_session_brief` includes a prompt directive Claude consumes — e.g. `"Log today's subjective markers (energy, mood, hunger)"` — which Claude uses to prompt Hugo before doing meaningful planning

This creates a soft-required pattern: the system functions without subjective markers but loudly signals their absence and downgrades confidence until logged.

---

## Ingestion scheduling

`APScheduler` config in `health-metrics-service/scheduler.py`:

- **Daily ingest job**: cron at `04:30 America/New_York` (after Oura/Whoop typically publish prior-day summaries)
  - Pulls yesterday's data from both APIs
  - Computes z-scores for last 14 days
  - Sets `ingestion_complete = TRUE` if both sources returned successfully
- **Retry job**: cron at `09:00` ET — retries any day in the last 7 with `ingestion_complete = FALSE`
- **Token refresh**: separate job every 50 minutes for Whoop OAuth token refresh

Use AsyncIO `APScheduler` (not threadpool) so it cooperates with FastAPI's event loop.

---

## Deployment (Railway)

### Environment variables

```
DATABASE_URL=postgresql+asyncpg://...        # Railway-provided
OURA_PERSONAL_TOKEN=...                      # static
WHOOP_CLIENT_ID=...
WHOOP_CLIENT_SECRET=...
WHOOP_REFRESH_TOKEN=...                      # bootstrap from initial OAuth flow
USER_ID=hugo
TIMEZONE=America/New_York
LOG_LEVEL=INFO
```

### `railway.toml`

```toml
[build]
builder = "DOCKERFILE"

[deploy]
startCommand = "uvicorn src.health_metrics.main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

### `mcp-unified-server` — add tools to existing deployment

No new service needed. Add the `tools/health/` module, redeploy. The MCP server connects to the same Postgres via a new read-only role:

```sql
CREATE ROLE mcp_reader WITH LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE health TO mcp_reader;
GRANT USAGE ON SCHEMA public TO mcp_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_reader;
GRANT INSERT, UPDATE ON manual_log, regulation_recommendations TO mcp_reader;
-- log_manual_entry needs write to manual_log
-- get_auto_regulation_status writes audit to regulation_recommendations
```

Set `DATABASE_URL_READONLY` in mcp-unified-server env pointing at the same DB with `mcp_reader` credentials.

---

## Implementation order

1. **Scaffold health-metrics-service**: pyproject, FastAPI app, /health endpoint, Dockerfile, Railway deploy — verify it boots
2. **Postgres + Alembic**: create migration with full schema, run against Railway Postgres, verify with psql
3. **Oura client**: implement `sources/oura.py`, write tests against fixture responses, then live test with personal token, single-day ingest
4. **Whoop client**: implement OAuth flow (one-time bootstrap script), refresh token logic, then ingest workflow
5. **Daily ingest job**: combine sources, normalize, write to DB, compute z-scores. Verify with a single date.
6. **Backfill endpoint**: `POST /backfill?days=30`, run once, validate 30 rows in `daily_metrics` with z-scores populated for days 14-30
7. **APScheduler**: wire daily + retry jobs, verify they fire in dev
8. **MCP tools — phase A**: add `tools/health/` to mcp-unified-server, implement `get_daily_snapshot` first (simplest), verify via Claude Desktop
9. **MCP tools — phase B**: implement `get_recovery_trend`, `get_strain_trend`, `get_weight_trend`, `get_workout_history` (all read-only queries)
10. **MCP tools — phase C**: implement `log_manual_entry` (write path), `get_auto_regulation_status` (regulation logic + audit write), `get_session_brief` (composite)
11. **Smoke test**: run through a full session with Claude — log a manual entry, call get_session_brief, verify regulation status returns sensible output

---

## Validation checklist

Before marking complete:

- [ ] `POST /backfill?days=30` populates 30 rows of `daily_metrics`
- [ ] Z-scores are computed for the most recent 14 days (days 1-14 may be null, which is expected)
- [ ] `ingestion_complete = TRUE` for at least 80% of backfilled days
- [ ] APScheduler daily job runs successfully against Railway in dev for 2 consecutive days
- [ ] Whoop OAuth token refresh works (force-expire access token, verify refresh)
- [ ] All 8 MCP tools callable via Claude Desktop, return well-formed Pydantic-shaped JSON
- [ ] `log_manual_entry` UPSERT works for partial updates (e.g. log weight in morning, log nutrition in evening, merged correctly)
- [ ] `get_auto_regulation_status` writes a row to `regulation_recommendations` every call
- [ ] `get_session_brief` correctly flags missing subjective markers
- [ ] Conservative bias verified: synthetic input with mild HRV depression (z = -0.6) returns "maintenance", not "deficit"
- [ ] Cold-start handling: with <14 days history, all tools return `confidence: "low"` and degrade gracefully (no crashes)

---

## Gotchas

1. **Oura "today" data is unreliable until ~10am** — Oura computes overnight summaries asynchronously. Always ingest *yesterday* on the daily job, not today.
2. **Whoop cycle != calendar day** — Whoop's "cycle" can span the calendar boundary if Hugo sleeps past midnight. Map cycles to dates by `cycle.start.date()` (wake date is what we care about for recovery).
3. **Whoop OAuth refresh tokens rotate** — every refresh call returns a *new* refresh token. Must persist back to env / a small `oauth_state` table. If you don't, you're locked out after the first refresh.
4. **Z-score requires variance** — if HRV is unusually stable for a few days (std ≈ 0), z-score returns 0.0 not None. Don't let downstream logic interpret 0.0 as "no data."
5. **Timezone handling** — store all timestamps as UTC in Postgres, but compute "day boundaries" in `America/New_York` for the daily roll-up. Use `pytz` or `zoneinfo`.
6. **Subjective fields stored as INTEGER, not 1-10 validated** — add Pydantic validators in `schemas.py` to enforce 1-10 range at the API layer before INSERT.
7. **Sleep debt sign convention (Whoop)** — Whoop returns sleep_debt as a positive number (how many minutes behind). Don't flip the sign.

---

## What's deferred to phase 2

- React dashboard (Recharts + shadcn/ui) — build after 2-3 weeks of data are flowing
- Apple Health integration — deferring per architecture discussion; only add if Oura+Whoop coverage proves insufficient
- Nutrition macro tracking integration (MyFitnessPal API or similar) — for now manual log via `log_manual_entry`
- Strength session detail capture (sets/reps/weight) — Ladder app data extraction is a separate workstream; for now the workout-level data from Whoop is sufficient for strain quantification
- Multi-user support — schema is multi-user-ready but no UI/auth needed yet

---

## After deploy: how this changes our planning sessions

Once live, my session-start protocol changes:

1. Call `get_session_brief()` — pulls today's state + missing inputs
2. If subjective markers missing, prompt Hugo to log them via `log_manual_entry`
3. Use `get_auto_regulation_status()` recommendation as the input to today's training/nutrition guidance
4. Reference `get_weight_trend()` weekly to recalibrate the deficit target based on revealed TDEE

This is the difference between "guessing what your recovery state is from your self-report" and "looking at your last 14 days of HRV trends and making a calibrated call."
