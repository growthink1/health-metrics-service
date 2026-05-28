# `/api/v1/health-events`

Manages health-event rows that the regulation engine treats as overrides on
the daily snapshot (e.g. "antibiotic course" forces zone-2-only).

Two endpoints:

- `POST /api/v1/health-events` — create a new event.
- `PATCH /api/v1/health-events/{event_id}` — update status, resolution, affects, or notes.

## Auth

`Authorization: Bearer <token>` — accepts either the dashboard token
(`HEALTH_API_TOKEN_DASHBOARD`) or the MCP token (`HEALTH_API_TOKEN_MCP`).
Missing or invalid → `401`.

## POST request

`Content-Type: application/json`

| Field                 | Type     | Constraint                | Notes                                  |
|-----------------------|----------|---------------------------|----------------------------------------|
| `user_id`             | `str`    |                           | Defaults to `"hugo"`.                  |
| `event_type`          | enum     | see below                 | Required.                              |
| `status`              | enum     | see below                 | Required.                              |
| `started_at`          | ISO date |                           |                                        |
| `expected_resolution` | ISO date |                           |                                        |
| `affects`             | `str[]`  |                           | Defaults to `[]`. Free-form tags.      |
| `notes`               | `str?`   |                           |                                        |

`event_type` enum: `dental_procedure`, `acute_infection`, `antibiotic_course`,
`fever`, `injury`, `scheduled_lab_draw`, `scheduled_dexa`, `scheduled_sleep_study`.

`status` enum: `active`, `pending`, `resolving`, `resolved`.

## PATCH request

Partial update — only the fields you send are applied. Path param: `event_id`
(UUID).

| Field                 | Type     | Notes                                |
|-----------------------|----------|--------------------------------------|
| `status`              | enum     | Same enum as POST.                   |
| `expected_resolution` | ISO date |                                      |
| `affects`             | `str[]`  | Replaces (does not merge).           |
| `notes`               | `str?`   |                                      |

## Response (both endpoints)

```json
{
  "id": "9c1b...uuid...",
  "user_id": "hugo",
  "event_type": "acute_infection",
  "status": "active",
  "started_at": "2026-05-26",
  "expected_resolution": "2026-06-01",
  "affects": ["training_intensity", "appetite"],
  "notes": "Doctor-verified, on amoxicillin"
}
```

## Status codes

| Code | Meaning                                            |
|------|----------------------------------------------------|
| 201  | (POST only) Event created.                         |
| 200  | (PATCH only) Event updated.                        |
| 401  | Missing/invalid bearer token.                      |
| 404  | (PATCH only) `event_id` not found.                 |
| 422  | Schema violation (e.g. unknown event_type).        |

## Cache invalidation

Both POST and PATCH delete `regulation_cache` for `(user_id, today)`. The
next `GET /api/v1/session-brief` recomputes and the new active-event list
flows into `daily_snapshot.active_events`, potentially changing the
`regulation_call` (e.g. acute infection forces `recover` state).

## Curl example (POST)

```bash
curl -X POST http://localhost:8000/api/v1/health-events \
  -H "Authorization: Bearer $HEALTH_API_TOKEN_DASHBOARD" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "hugo",
    "event_type": "acute_infection",
    "status": "active",
    "started_at": "2026-05-26",
    "expected_resolution": "2026-06-01",
    "affects": ["training_intensity"],
    "notes": "Sinus infection, day 2 of amoxicillin"
  }'
```

## Curl example (PATCH — resolve an event)

```bash
curl -X PATCH http://localhost:8000/api/v1/health-events/9c1b... \
  -H "Authorization: Bearer $HEALTH_API_TOKEN_DASHBOARD" \
  -H "Content-Type: application/json" \
  -d '{"status": "resolved"}'
```
