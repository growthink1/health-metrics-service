# POST `/api/v1/manual-entry`

Upserts a single-day manual log row (weight, nutrition macros, and 1–10 subjective
markers). Idempotent on `(user_id, log_date)` — a second POST for the same date
patches the existing row without nulling other fields.

## Auth

`Authorization: Bearer <token>` — accepts either the dashboard token
(`HEALTH_API_TOKEN_DASHBOARD`) or the MCP token (`HEALTH_API_TOKEN_MCP`).
Missing or invalid → `401`.

## Request

`Content-Type: application/json`

The payload accepts BOTH DB-aligned names and semantic MCP-friendly aliases
(`populate_by_name=True`). Aliases exist so MCP wrappers can speak natural
field names without an extra translation layer.

| Native (DB-aligned)     | Alias            | Type      | Constraint | Notes                                  |
|-------------------------|------------------|-----------|------------|----------------------------------------|
| `user_id`               | —                | `str`     |            | Defaults to `"hugo"`.                  |
| `log_date`              | `entry_date`     | ISO date  |            | Defaults to today.                     |
| `weight_lbs`            | —                | `float?`  |            |                                        |
| `kcal_consumed`         | —                | `int?`    |            |                                        |
| `protein_g`             | —                | `int?`    |            |                                        |
| `fat_g`                 | —                | `int?`    |            |                                        |
| `carbs_g`               | —                | `int?`    |            |                                        |
| `subjective_energy`     | `energy_1_10`    | `int?`    | 1 ≤ x ≤ 10 |                                        |
| `subjective_mood`       | `mood_1_10`      | `int?`    | 1 ≤ x ≤ 10 |                                        |
| `subjective_hunger`     | `hunger_1_10`    | `int?`    | 1 ≤ x ≤ 10 |                                        |
| `soreness_1_10`         | —                | `int?`    | 1 ≤ x ≤ 10 |                                        |
| `sleep_subjective_1_10` | —                | `int?`    | 1 ≤ x ≤ 10 |                                        |
| `notes`                 | —                | `str?`    |            | Free-text                              |

Mixed payloads (some alias, some native) work as long as a given field appears
only once.

## Response

`201 Created`. Body uses DB-aligned names (Pydantic serializes by field name
unless told otherwise).

```json
{
  "id": 42,
  "user_id": "hugo",
  "log_date": "2026-05-28",
  "weight_lbs": 180.5,
  "kcal_consumed": null,
  "protein_g": null,
  "fat_g": null,
  "carbs_g": null,
  "subjective_energy": 7,
  "subjective_mood": 8,
  "subjective_hunger": 6,
  "soreness_1_10": null,
  "sleep_subjective_1_10": null,
  "notes": null
}
```

## Status codes

| Code | Meaning                                                    |
|------|------------------------------------------------------------|
| 201  | Row inserted or updated.                                   |
| 401  | Missing/invalid bearer token.                              |
| 422  | Constraint violation (e.g. subjective marker outside 1–10).|

## Cache invalidation

After the upsert commits, the row in `regulation_cache` for
`(user_id, today)` is deleted. The next `GET /api/v1/session-brief` cache-misses
and recomputes — picking up the new subjective markers and potentially flipping
`confidence` from `medium` → `high` (when this fills the only remaining gap).

## Curl example (alias names)

```bash
curl -X POST http://localhost:8000/api/v1/manual-entry \
  -H "Authorization: Bearer $HEALTH_API_TOKEN_DASHBOARD" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "hugo",
    "entry_date": "2026-05-28",
    "energy_1_10": 7,
    "mood_1_10": 8,
    "hunger_1_10": 6,
    "soreness_1_10": 3
  }'
```

## Curl example (native names)

```bash
curl -X POST http://localhost:8000/api/v1/manual-entry \
  -H "Authorization: Bearer $HEALTH_API_TOKEN_DASHBOARD" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "hugo",
    "log_date": "2026-05-28",
    "weight_lbs": 180.5,
    "kcal_consumed": 2200
  }'
```
