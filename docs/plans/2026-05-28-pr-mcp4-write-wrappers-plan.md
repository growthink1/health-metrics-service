# PR-MCP4 — write wrappers + aliases + docs

**Date:** 2026-05-28
**Status:** In flight (this PR)
**Sibling PR:** `pr-mcp4/write-wrappers` in `~/mcp-unified-server` (independent)
**Branch:** `pr-mcp4/aliases-plus-docs`

## Goal

Ship the MCP write surface for `health-metrics-service` so Claude can log
manual entries, meals, and health events conversationally. PR-MCP4 is a
follow-up to the session-brief sprint (PRs 1–6 already merged) — the
originally-planned PR 4 already shipped write endpoints; this PR adds the
ergonomics around them (semantic aliases, integration coverage, reference
docs) plus a design note for the next big sub-feature (`regulation_overrides`).

## Scope

### In-scope (this PR — health-metrics-service)

1. **Pydantic aliases** on `ManualEntryPayload` (`entry_date`, `energy_1_10`,
   `mood_1_10`, `hunger_1_10`). `populate_by_name=True` keeps the existing
   DB-aligned shape working — pure backward-compat add.
2. **Integration round-trip test** at `tests/test_integration_brief_round_trip.py`
   that proves POST `/manual-entry` → cache invalidates → next
   `/session-brief` recomputes → confidence flips `medium` → `high`.
3. **Per-endpoint reference docs** at `docs/api/{manual-entry,meals,health-events}.md`.
4. **Spec §13 design note** for `regulation_overrides` (PR 7, doc-only here).
5. **This plan.**

### In-scope (sibling PR — mcp-unified-server)

3 MCP write wrappers — translate semantic field names → DB-aligned names on
the wire, then call the existing REST endpoints:

- [ ] `log_manual_entry(user_id?, entry_date?, weight_lbs?, kcal_consumed?, protein_g?, fat_g?, carbs_g?, energy_1_10?, mood_1_10?, hunger_1_10?, soreness_1_10?, sleep_subjective_1_10?, notes?)`
- [ ] `log_meal(user_id?, meal_date?, meal_time?, meal_name?, kcal?, protein_g?, fat_g?, carbs_g?, notes?, photo_path?)`
- [ ] `upsert_health_event(user_id?, event_type, status, started_at?, expected_resolution?, affects?, notes?, event_id?)`

Registration in `HealthMetricsTools.get_tools()`, dispatch in `execute()`,
tests in `tests/test_health_metrics.py`.

### Out of scope (explicit, prevents drift)

- **PR 7 (`regulation_overrides`)** — designed in spec §13 only; no schema,
  no engine wiring, no endpoints, no MCP tools in this PR.
- `created_by` audit columns — deferred per the drift conversation decision (2a);
  no current consumer.
- A semantic rename (drop `subjective_*` and `log_date` outright) — explicitly
  rejected; aliases keep the dashboard / chat / existing tests green.

## Sequence

1. Aliases on `ManualEntryPayload` (this PR).
2. Round-trip integration test (this PR).
3. Per-endpoint reference docs (this PR).
4. Spec §13 design note (this PR).
5. Sibling PR ships MCP wrappers — can land before or after; they translate
   semantic→DB on the wire.

## Test fixture list

| Test                                                         | Location                                        |
|--------------------------------------------------------------|-------------------------------------------------|
| `test_manual_entry_accepts_entry_date_alias`                 | `tests/test_routes_manual_entry.py`             |
| `test_manual_entry_mixed_aliases_and_native`                 | `tests/test_routes_manual_entry.py`             |
| `test_manual_entry_round_trip_flips_confidence`              | `tests/test_integration_brief_round_trip.py`    |

Existing 5 manual-entry tests + 1 new round-trip + 2 new alias tests =
ratchet from 208 → 211 in the full suite.

## Dependency graph

```
   PR-MCP4a (this)  <-->  PR-MCP4b (sibling, mcp-unified-server)
       │                       │
       └── INDEPENDENT ────────┘
              │
              ▼
          merge to main
              │
              ▼
       PR 7 (regulation_overrides) — future sprint
```

PR-MCP4a ↔ PR-MCP4b are independent because the wrappers translate semantic →
DB on the wire client-side. The alias landing isn't a prerequisite for the
wrappers — they'd work today against the existing DB-aligned shape. The
aliases just mean the wrappers can ALSO speak directly without the
translation layer if a future caller prefers semantic field names end-to-end.

## Gates

- `pytest -q` — full suite green (target 211 tests).
- `pytest tests/regulation/test_engine.py --cov=health_metrics.regulation.engine --cov-branch --cov-fail-under=100` — engine.py 100% line + branch coverage maintained.
- `ruff check` + `ruff format --check` clean on touched files.
