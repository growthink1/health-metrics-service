# Session Brief + Auto-Regulation Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (per spec §1 workflow). Each PR: implementer subagent → spec review → Code Reviewer → fix → re-review. CI gates: ruff · mypy-strict · pytest with 100% coverage on `src/health_metrics/regulation/`. Conventional commits. NO co-author trailer.

**Goal:** Ship the daily session-brief + auto-regulation engine described in `docs/superpowers/specs/2026-05-26-session-brief-design.md` across 6 PRs (~1 Claude-week). PR 1–5 land in `growthink1/health-metrics-service`; PR 6 lands the MCP consumer tool in `growthink1/mcp-unified-server`.

**Repos:**
- `growthink1/health-metrics-service` at `~/code/health-metrics-service` — PRs 1–5
- `growthink1/mcp-unified-server` at `~/mcp-unified-server` — PR 6

**Branch model:** PR-per-task (feature branches off `main`). Direct-to-main NOT used here — each PR opens via `gh pr create`.

**Layout note:** The spec uses `app/regulation/` paths; this repo uses `src/health_metrics/`. All spec paths translated below. CI coverage target = `src/health_metrics/regulation/`.

---

## 1. Open decisions (resolve before PR 1 lands)

Pushback items surfaced before plan write — final-locked answers go here.

| # | Decision | Recommendation | Locked? |
|---|---|---|:--:|
| 1 | Module layout | `src/health_metrics/regulation/` subpackage (engine.py, schemas.py, cache.py, legacy.py). Routes in `routes/session_brief.py`. | TBD |
| 2 | Cutover for existing `regulate()` | PR 5 retires it: rename PR 5 to "trend endpoints + retire legacy `regulate()`". Adapter or hard cutover for 3 callers. | TBD |
| 3 | `oura_status` / `whoop_status` already exist | `ADD COLUMN IF NOT EXISTS` is idempotent — no action needed, note in PR 1 body. | Default-locked |
| 4 | `user.age` source | `src/health_metrics/users.py` constants module: `{ "hugo": 44, "andrea": <TBD> }`. Promote to `users` table in a follow-up. | TBD |
| 5 | z-score window | PR 1 leaves existing z-scores alone. PR 2 ships `scripts/recompute_unified_z.py` (14-day rolling + 7-day warmup), run manually after PR 2. Confirm actual current window during PR 2 implementation. | TBD |
| 6 | MCP downstream consumer | **In scope as PR 6.** New `get_session_brief()` tool in `growthink1/mcp-unified-server` calling `/api/v1/session-brief`. Blocks on PR 3. Separate auth principal (`HEALTH_API_TOKEN_MCP`) for audit-trail isolation per spec §5. | Locked (Hugo override 2026-05-26) |
| 7 | Spec open-decision #1 (z-score window) | Same as #5 above. 14d rolling + 7d warmup. | Per spec §10.1 |
| 8 | Spec open-decision #2 (couple brief) | Per-user `get_session_brief(user)` for v0.1. `get_couple_brief()` deferred. | Per spec §10.2 |

---

## 2. PR sequence + dependency graph

```
PR 1 (schema)
  └─→ PR 2 (engine + fixtures) ← no other deps
        ├─→ PR 3 (endpoint + cache + post-ingest hook)
        │     └─→ PR 6 (mcp-unified-server get_session_brief tool)
        ├─→ PR 4 (write endpoints + cache invalidation)
        └─→ PR 5 (trend endpoints + retire legacy regulate())

PR 1 blocks PR 2, 3, 4, 5, 6.
PR 2 blocks PR 3 + PR 5.
PR 3 blocks PR 6.
PR 3 ↔ PR 4 are independent given PR 2 is in.
PR 6 ↔ PR 4 ↔ PR 5 can land in any order after PR 3 ships.
```

| PR # | Repo | Title | Spec § | Branch | Files touched | Tests added | Blocks |
|---|---|---|---|---|---|---|---|
| 1 | health-metrics-service | `feat(db): health_events + regulation_cache + manual_log subjective markers` | §3.1, §3.2 | `pr1/regulation-schema` | alembic migration + `src/health_metrics/models.py` + `tests/test_models_migration.py` canary update | 2 ORM canary tests + 1 forward-upgrade integration test | All others |
| 2 | health-metrics-service | `feat(regulation): compute_regulation engine + schemas + fixtures` | §3.3, §4, §7 | `pr2/regulation-engine` | `src/health_metrics/regulation/{engine.py, schemas.py, legacy.py}` + 7 fixture JSON files + `scripts/recompute_unified_z.py` | 7 fixture tests + branch coverage tests (100% on `engine.py`) | PR 3, PR 5 |
| 3 | health-metrics-service | `feat(api): /session-brief + regulation_cache + post-ingest hook` | §5, §6 | `pr3/session-brief-endpoint` | `src/health_metrics/{routes/session_brief.py, regulation/cache.py, jobs/scheduler.py}` | endpoint integration + cache TTL + post-ingest hook idempotency | PR 6 |
| 4 | health-metrics-service | `feat(api): write tools — manual_entry, meals, health_events` | §5 | `pr4/write-endpoints-cache-invalidation` | `src/health_metrics/routes/{manual_entry.py, health_events.py}` + cache invalidation hooks | write-path + cache-invalidation tests | — |
| 5 | health-metrics-service | `feat(api): trend endpoints + retire legacy regulate()` | §5, §10 | `pr5/trends-plus-legacy-retire` | `src/health_metrics/routes/{workouts_history.py, weight_trend.py}` + migrate `chat_prompts.py`, `jobs/daily_goals.py`, `routes/api.py` callers from `regulate()` → `compute_regulation()` adapter | trend endpoint tests + caller-migration tests | Monthly review workflow |
| 6 | mcp-unified-server | `feat(tools): get_session_brief — daily check-in tool for claude.ai` | §1 (consumer), §5 (auth) | `pr6/session-brief-tool` | new `tools/health/session_brief.py` + register in tool index + `.env.example` keys + `tools/health/__init__.py` if missing | HTTP-mock unit test + auth-header presence test + JSON-shape contract test against the SessionBrief pydantic schema (vendored or duplicated lightweight to avoid health-metrics-service dependency) | claude.ai daily brief workflow |

---

## 3. PR 1 scope — alembic migration only

**Files touched:**

- `alembic/versions/<rev>_session_brief_schema.py` (new migration, parent = `d14bc789212f` from v4 T1)
- `src/health_metrics/models.py` (append `HealthEvent`, `RegulationCache`; add columns to `ManualLog`)
- `tests/test_models_migration.py` (canary set update + 2 new tests)

**SQL operations (idempotent):**

```sql
-- New tables
CREATE TABLE health_events ( ... );  -- per spec §3.1
CREATE INDEX health_events_user_status_idx ON health_events (user_id, status);
CREATE INDEX health_events_expected_idx ON health_events (expected_resolution)
    WHERE status IN ('pending','active','resolving');

CREATE TABLE regulation_cache ( ... );  -- per spec §3.1
CREATE INDEX regulation_cache_cached_at_idx ON regulation_cache (cached_at);

-- ALTER manual_log
ALTER TABLE manual_log
    ADD COLUMN IF NOT EXISTS soreness_1_10        SMALLINT,
    ADD COLUMN IF NOT EXISTS sleep_subjective_1_10 SMALLINT;

-- ALTER daily_metrics (already exist; no-op via IF NOT EXISTS)
ALTER TABLE daily_metrics
    ADD COLUMN IF NOT EXISTS oura_status  TEXT NOT NULL DEFAULT 'ok',
    ADD COLUMN IF NOT EXISTS whoop_status TEXT NOT NULL DEFAULT 'ok';
```

**ORM additions in `models.py`:**

- `HealthEvent(Base)` — UUID primary key (`gen_random_uuid()`), event_type CHECK constraint, status CHECK constraint, `affects` as `ARRAY(Text)`, the two indexes above as `__table_args__`.
- `RegulationCache(Base)` — composite PK `(user_id, as_of_date)`, `brief_json` as JSONB, `cached_at` index.
- `ManualLog` gains `soreness_1_10: Mapped[Optional[int]]` + `sleep_subjective_1_10: Mapped[Optional[int]]`.

**Tests:**

1. `tests/test_models_migration.py::test_all_expected_tables_registered` — add `"health_events"` + `"regulation_cache"` to the expected set.
2. `tests/test_models_migration.py::test_health_event_status_check_constraint` — new test verifying the CHECK constraint rejects invalid status values.
3. `tests/test_models_migration.py::test_regulation_cache_composite_pk` — new test verifying the PK is `(user_id, as_of_date)`.

**Local verification commands (in PR description):**

```bash
cd ~/code/health-metrics-service
DB_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)
DATABASE_URL="$DB_URL" alembic upgrade head     # apply
DATABASE_URL="$DB_URL" alembic downgrade -1     # backward
DATABASE_URL="$DB_URL" alembic upgrade head     # re-apply (idempotent confirmation)
DATABASE_URL="$DB_URL" pytest tests/test_models_migration.py -v
```

**Manual backfill SQL (in PR body, NOT in the migration):**

```sql
-- Hugo's pending dental procedure event
INSERT INTO health_events (user_id, event_type, status, started_at, expected_resolution, affects, notes)
VALUES (
    'hugo',
    'dental_procedure',
    'pending',
    '2026-05-23',
    '2026-06-04',
    ARRAY['no_deficit','no_z4_plus','rpe_cap_7','watch_jaw_load'],
    'Pre-extraction window; per spec §12'
);
```

Optional retroactive backfill:

```sql
-- May 14–18 tooth infection peak (retroactive fixture matching)
INSERT INTO health_events (user_id, event_type, status, started_at, expected_resolution, affects, notes)
VALUES (
    'hugo',
    'acute_infection',
    'resolved',
    '2026-05-14',
    '2026-05-18',
    ARRAY['no_training','rest_priority'],
    'Tooth infection peak; matches `2026-05-17-infection-peak.json` fixture'
);
```

**CI gates:** ruff, mypy-strict, pytest. (100% coverage rule applies to PR 2+ — PR 1's only Python is the migration + ORM mappings, where line coverage isn't meaningful.)

---

## 4. Fixture file list (PR 2 ships these)

All fixtures live at `tests/fixtures/days/`. Each is a JSON file containing a single-day snapshot (daily_metrics row + recent workouts + active health events + computed trends). Verbatim from real export data — no synthesis.

| # | Path | Source data | Expected state | Expected training | Expected overrides ⊇ |
|---|---|---|---|---|---|
| 1 | `tests/fixtures/days/2026-05-17-infection-peak.json` | Real row from prod (Recovery 6, RHR z +4.33) + manual `acute_infection` event | `MAINTENANCE_ILLNESS` | `REST` | `{no_training}` |
| 2 | `tests/fixtures/days/2026-05-24-sleep-crash.json` | Real row (sleep 4.65h, Recovery 38) | `MAINTENANCE_SLEEP_DEFICIT` | `Z2_ONLY` | `{no_lift_today}` |
| 3 | `tests/fixtures/days/2026-05-26-pre-extraction.json` | Real row (Recovery 62, HRV z₃ +0.91) + pending dental June 4 | `MAINTENANCE_PRE_PROCEDURE` | `VOLUME_MINUS_20` | `{no_deficit_pre_procedure, no_z4_plus, rpe_cap_7, watch_jaw_load}` |
| 4 | `tests/fixtures/days/2026-05-25-post-z5-spike.json` | Synthetic from real day (max HR 171 yesterday, Recovery 74 today) + pending dental | `MAINTENANCE_PRE_PROCEDURE` | `VOLUME_MINUS_20` | `{..., no_z4_plus}` (override path) |
| 5 | `tests/fixtures/days/green-baseline-hugo.json` | All-green snapshot, 30d history, no events | `DEFICIT` | `FULL_PROGRESSION` | `{}` |
| 6 | `tests/fixtures/days/cold-start-andrea.json` | <14d history scenario | `DEFICIT_CONSERVATIVE` | `FULL_NO_PROGRESSION` | `{}` |
| 7 | `tests/fixtures/days/hrv-depression-3d.json` | z₃ −1.4, 4 consecutive days below baseline | `MAINTENANCE_HRV_DEPRESSION` | `VOLUME_MINUS_30_NO_HIIT` | `{}` |

PR 2's `tests/regulation/test_engine.py` runs each fixture through `compute_regulation()` and asserts the expected fields.

---

## 5. PR 6 scope — MCP `get_session_brief` tool

**Repo:** `growthink1/mcp-unified-server` at `~/mcp-unified-server`
**Branch:** `pr6/session-brief-tool`
**Blocks on:** PR 3 (needs the `/api/v1/session-brief` endpoint live in prod)

**Tool shape:**

```python
# tools/health/session_brief.py (or wherever the existing tool layout lives — confirm at dispatch time)
from tools.health.types import SessionBrief

@tool(name="get_session_brief",
      description="Read Hugo or Andrea's daily auto-regulation brief: recovery state, training prescription, kcal target, active health events, missing inputs, confidence. Use at the start of any daily check-in conversation. Defaults to user_id='hugo'.")
async def get_session_brief(user_id: str = "hugo") -> SessionBrief:
    """Returns the same SessionBrief shape the dashboard renders.
    Cache-aware (server side) — calls return in <100ms for warm cache."""
    url = os.environ["HEALTH_API_URL"].rstrip("/") + f"/api/v1/session-brief?user_id={user_id}"
    headers = {"Authorization": f"Bearer {os.environ['HEALTH_API_TOKEN_MCP']}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return SessionBrief.model_validate(resp.json())
```

**Auth principal:** Per spec §5 — MCP token is a separate principal from the dashboard token so the audit log distinguishes callers. New env var on the MCP server: `HEALTH_API_TOKEN_MCP`. PR 3 prep work: provision the token + record it in 1Password (or wherever Hugo manages secrets) so PR 6 can set it as a Railway/local env var.

**Tests:**

- `tests/health/test_session_brief.py::test_get_session_brief_includes_auth_header` — mock `httpx.AsyncClient.get`, assert the `Authorization: Bearer <token>` header is set.
- `tests/health/test_session_brief.py::test_get_session_brief_passes_user_id_query_param` — assert the URL contains `?user_id=andrea` when called with `user_id="andrea"`.
- `tests/health/test_session_brief.py::test_get_session_brief_raises_on_4xx` — mock a 401, assert `httpx.HTTPStatusError`.
- `tests/health/test_session_brief.py::test_get_session_brief_shape_contract` — mock a successful response with the canonical `SessionBrief` shape, assert returned dict has top-level keys matching what the spec §3.3 names lock in (`as_of_date`, `regulation_call`, `daily_snapshot`, `recent_workouts`, `weight_trend`, `active_events`, `flags`, `missing_inputs`, `confidence`).

**Schema strategy: vendored `SessionBrief` model** (locked Hugo 2026-05-26). The MCP server does NOT add `health-metrics-service` as a Python dependency (the spec's Invariant #2 keeps the engine a pure function, and dependency-linking the consumer breaks deploy isolation). Instead:

- `tools/health/types.py` vendors the `SessionBrief` + transitive types (`RegulationCall`, `DailySnapshot`, `WorkoutSummary`, `TrendSummary`, `HealthEvent`, `Flag`, `WeightTrend`, `MissingInput`, plus the `RegulationState` / `TrainingModifier` enums) — copy-pasted from `src/health_metrics/regulation/schemas.py` at PR 2 time, with a top-of-file comment naming the upstream SHA so future drift is auditable.
- The tool parses the HTTP response into `SessionBrief.model_validate(json)` and returns the typed instance.
- The shape-contract test asserts `model_validate` succeeds on a canonical fixture — drift between upstream and vendored schemas surfaces as a Pydantic validation error in CI.
- When the upstream schema changes, the vendoring update is a follow-up PR (small, mechanical) on the MCP repo. Acceptable cost in exchange for typed unpacking on the consumer side.

**Operational notes:**

- Add `HEALTH_API_URL` + `HEALTH_API_TOKEN_MCP` to the MCP server's `.env.example` and (if applicable) Railway env config.
- Restart the MCP server after PR 6 ships so the new tool registers.
- Smoke test: call `get_session_brief()` in a claude.ai conversation, confirm the returned brief renders cleanly into Claude's response.

**CI gates:** Same as the rest of the sprint — ruff, mypy-strict, pytest. Coverage target = the new `tools/health/` subdirectory.

---

## 6. Stop point

Per Hugo's instruction: after PR 1 ships (migration runs forward + backward cleanly + canary tests green + PR opened + reviewer assigned), **STOP**. Do not auto-start PR 2.

Hugo reviews the migration. Pushback items above get resolved (locked in §1). Then PR 2 starts.

---

*Plan committed alongside the spec at `docs/superpowers/specs/2026-05-26-session-brief-design.md`. Following the TQStarling Phase 1 execution pattern documented in §9A of CLAUDE_MEMORY.md.*
