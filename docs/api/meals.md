# POST `/api/v1/meals`

Append-only meal log. Each POST creates a new row — no upsert, no dedup. Used
by the dashboard meal-entry drawer and by the MCP `log_meal` wrapper.

## Auth

`Authorization: Bearer <token>` — accepts either the dashboard token
(`HEALTH_API_TOKEN_DASHBOARD`) or the MCP token (`HEALTH_API_TOKEN_MCP`).
Missing or invalid → `401`.

## Request

`Content-Type: application/json`

| Field          | Type     | Constraint | Notes                                                         |
|----------------|----------|------------|---------------------------------------------------------------|
| `user_id`      | `str`    |            | Defaults to `"hugo"`.                                         |
| `meal_date`    | ISO date |            | Defaults to today.                                            |
| `meal_time`    | `str?`   | `HH:MM[:SS]` | Parsed via `time.fromisoformat`. Omit for "no specific time". |
| `meal_name`    | `str?`   |            | Free-text (e.g. "post-workout shake").                        |
| `kcal`         | `int?`   |            |                                                               |
| `protein_g`    | `int?`   |            |                                                               |
| `fat_g`        | `int?`   |            |                                                               |
| `carbs_g`      | `int?`   |            |                                                               |
| `notes`        | `str?`   |            | Free-text.                                                    |
| `photo_path`   | `str?`   |            | Railway Object Storage path (v3 capture flow).                |
| `source`       | `str`    |            | Defaults to `"api"`. MCP wrappers should pass `"mcp"`.        |

No field aliases — `meal_date` is the only date field and is already a
sensible MCP-facing name.

## Response

`201 Created`.

```json
{
  "id": 17,
  "user_id": "hugo",
  "meal_date": "2026-05-28",
  "meal_time": "12:30:00",
  "meal_name": "lunch",
  "kcal": 650,
  "protein_g": 45,
  "fat_g": 22,
  "carbs_g": 60,
  "notes": null,
  "photo_path": null,
  "source": "api"
}
```

## Status codes

| Code | Meaning                                            |
|------|----------------------------------------------------|
| 201  | Meal row inserted.                                 |
| 401  | Missing/invalid bearer token.                      |
| 422  | Schema violation (e.g. malformed `meal_time`).     |

## Cache invalidation

After the insert commits, the row in `regulation_cache` for
`(user_id, today)` is deleted. The next `GET /api/v1/session-brief` will
recompute. Meals influence the brief indirectly via the meals→`weight_trend`
TDEE-revealing math when the user also logs weight in `/manual-entry`.

## Curl example

```bash
curl -X POST http://localhost:8000/api/v1/meals \
  -H "Authorization: Bearer $HEALTH_API_TOKEN_DASHBOARD" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "hugo",
    "meal_date": "2026-05-28",
    "meal_time": "12:30",
    "meal_name": "lunch",
    "kcal": 650,
    "protein_g": 45
  }'
```
