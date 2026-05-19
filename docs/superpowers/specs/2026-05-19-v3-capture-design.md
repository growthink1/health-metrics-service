# v3 — Day-of-Life Capture Expansion: Design Spec

**Date:** 2026-05-19
**Status:** Approved (architecture); plan to follow
**Repos affected:** `growthink1/health-metrics-service` + `growthink1/health-metrics-dashboard`
**Predecessor specs:**
- `2026-05-14-health-metrics-dashboard-design.md` (Plan 2)
- `2026-05-18-railway-deploy-design.md` (Plan 3)
- `2026-05-18-v2-chat-athletic-design.md` (v2 chat + athletic)

## Goal

Make the dashboard capture every kind of day-of-life event hands-free: meals (with photos via Claude vision), workouts done off-strap, individual sets during lifting sessions, and voice as the primary chat input modality. The capture flow is unified: chat drawer + voice + photo + tool-use confirmation.

## Scope

In:
- **Meal logging** — new `meals` table, per-meal nutrition rows (kcal + macros + optional photo + notes). Day-aggregate (`manual_log.kcal_consumed` etc.) auto-recomputes from meal rows.
- **Photo upload for meal logging via Claude vision** — drawer paperclip → file picker → multimodal chat → Claude estimates macros from the image → user confirms → meal row created. Photo bytes persisted to Railway Object Storage; the meal row keeps the bucket key only.
- **Manual workout logging** — chat tool that writes to the existing `workouts` table with `source='manual'`. No new table for the workout itself; the existing schema accommodates manual rows.
- **Per-set workout logging** — new `workout_sets` table (exercise, set #, reps, weight, RPE, notes). Each set FKs to a `workouts` row. UI: expandable rows on the existing `/workouts` page.
- **Voice input** — mic icon in the ChatDrawer input area. Web Speech API (`SpeechRecognition`). Transcript populates the chat input; user can edit before sending.

Out (deferred to v4 or later):
- Persistent chat history across sessions.
- Audit log table for tool-use writes (meals are auditable via `created_at` + `source` field; full audit deferred).
- Mobile-first responsive layout — capture works on desktop; v4 carries mobile.
- Compare-period view, workout overlays on charts, CSV/PDF export — all carry from prior backlog.
- Meal templates / saved meals.
- Workout programs / training-plan templates.
- Cross-browser voice fallback (server-side transcription) — Chrome/Safari/Edge cover the user's setup.
- Public meal-share / social — single-user tool.

## Non-goals

- Multi-user. Still single-user (Hugo), HTTP Basic Auth gated.
- Real-time set tracking (push notifications between sets, rest timers). Just CRUD.
- Estimating macros from voice description alone — voice goes through normal chat → Claude (no vision); macros estimated from the text content. Same flow as text chat.

## Architecture

```
        Browser (Hugo, Chrome/Safari)
        ┌────────────────────────────────────────────┐
        │  Dashboard (Next.js)                       │
        │  ┌──────────────────┬───────────────────┐  │
        │  │ Hero + tiles     │ ChatDrawer        │  │
        │  │ Workouts (now    │  ┌─ user ─────┐   │  │
        │  │  expandable      │  │ [📷 chip]  │   │  │
        │  │  per-set rows)   │  │ "log this  │   │  │
        │  │                  │  │  dinner"   │   │  │
        │  │                  │  └────────────┘   │  │
        │  │                  │  🎤 + 📎 + 📤    │  │
        │  └──────────────────┴───────────────────┘  │
        └────────────────────────────────────────────┘
                          │
                          ▼ same-origin proxy
        ┌────────────────────────────────────────────┐
        │  Backend (FastAPI)                         │
        │   POST /api/chat       multimodal SSE      │
        │   POST /api/meals      JSON create         │
        │   POST /api/meals/upload   multipart       │
        │   GET  /api/meals?date=...                 │
        │   GET  /api/meals/{id}/photo               │
        │   POST /api/workouts/manual                │
        │   POST /api/workouts/{id}/sets             │
        │   GET  /api/workouts/{id}/sets             │
        │                                            │
        │  New tools: log_meal, log_manual_workout,  │
        │             log_workout_set, get_recent_meals │
        └─────────┬─────────────────────┬────────────┘
                  │                     │
                  ▼                     ▼
           Railway Postgres     Railway Object Storage
           (meals, workout_sets, (s3://health-metrics-
            existing tables)      photos/meals/<uuid>.jpg)
```

Three sub-systems:
- **Capture (frontend):** voice + photo + chat in one input.
- **Storage + APIs (backend):** new tables, new routes, photo proxy.
- **Vision + tool-use (Claude via chat):** multimodal messages, new tools, automatic macro estimation.

## Components

### 1. Backend — new tables (alembic migration)

```sql
CREATE TABLE meals (
  id            BIGSERIAL PRIMARY KEY,
  user_id       TEXT NOT NULL,
  meal_date     DATE NOT NULL,
  meal_time     TIME,
  meal_name     TEXT,
  kcal          INTEGER,
  protein_g     INTEGER,
  fat_g         INTEGER,
  carbs_g       INTEGER,
  notes         TEXT,
  photo_path    TEXT,
  source        TEXT NOT NULL DEFAULT 'chat',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_meals_user_date ON meals(user_id, meal_date);

CREATE TABLE workout_sets (
  id            BIGSERIAL PRIMARY KEY,
  user_id       TEXT NOT NULL,
  workout_id    BIGINT NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
  set_number    INTEGER NOT NULL,
  exercise      TEXT NOT NULL,
  reps          INTEGER NOT NULL,
  weight_lbs    NUMERIC(6, 2),
  rpe           NUMERIC(3, 1),
  notes         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_workout_sets_workout ON workout_sets(workout_id);
```

No changes to the existing `workouts` table — manual workout rows just use `source='manual'` and `raw=NULL`.

### 2. Backend — Object Storage client

New module `src/health_metrics/storage.py` wrapping `boto3.client("s3", ...)` with config from env vars (`S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`). Single responsibility: upload bytes → return key; stream bytes by key. No URLs leaked to the client — all photo access goes through a proxy route.

Add `boto3>=1.34` to `requirements.txt`.

### 3. Backend — new routes

`src/health_metrics/routes/meals.py`:
- `POST /api/meals` — JSON body `{date, time?, meal_name?, kcal, protein_g?, fat_g?, carbs_g?, notes?, photo_path?, source?}` → insert row, recompute day aggregate.
- `POST /api/meals/upload` — multipart; uploads to bucket, returns `{photo_path}`. Photo bytes never touch Postgres.
- `GET /api/meals?date=YYYY-MM-DD` — list day's meals (oldest first by meal_time then created_at).
- `GET /api/meals/{id}/photo` — stream the photo from the bucket; same-origin auth gate already in place via dashboard proxy + middleware.
- `DELETE /api/meals/{id}` — delete row + recompute day aggregate; photo blob deleted asynchronously from the bucket.

`src/health_metrics/routes/workout_sets.py`:
- `POST /api/workouts/{workout_id}/sets` — body `{exercise, reps, weight_lbs?, rpe?, notes?}` OR batch `{sets: [...]}` for multi-set commits.
- `GET /api/workouts/{workout_id}/sets` — list sets ordered by set_number.
- `DELETE /api/workouts/sets/{set_id}` — single set delete.

`src/health_metrics/routes/workouts_manual.py` (new sibling of the existing workouts read route):
- `POST /api/workouts/manual` — body `{date, sport_name, duration_min, strain?, kcal?, notes?}` → insert workouts row with `source='manual'`, generated UUID for `source_id`, `raw=NULL`. Returns the new workout's id so subsequent set logs can attach.

### 4. Backend — chat tools (extend `chat_tools.py`)

Three new write tools + one read tool:

```python
TOOL_DEFINITIONS.extend([
    {
        "name": "log_meal",
        "description": "Write a meal entry with macros + optional photo path. The user MUST confirm before this runs. If photo_path is provided, it must be a bucket key returned earlier by the upload flow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date":       {"type": "string"},
                "time":       {"type": "string"},
                "meal_name":  {"type": "string"},
                "kcal":       {"type": "integer", "minimum": 0, "maximum": 10000},
                "protein_g":  {"type": "integer", "minimum": 0, "maximum": 1000},
                "fat_g":      {"type": "integer", "minimum": 0, "maximum": 1000},
                "carbs_g":    {"type": "integer", "minimum": 0, "maximum": 2000},
                "notes":      {"type": "string"},
                "photo_path": {"type": "string"},
            },
            "required": ["date", "kcal"],
        },
    },
    {
        "name": "log_manual_workout",
        "description": "Log a workout the user did off-strap (Whoop didn't capture it). The user MUST confirm before this runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date":         {"type": "string"},
                "sport_name":   {"type": "string"},
                "duration_min": {"type": "integer", "minimum": 1, "maximum": 600},
                "strain":       {"type": "number", "minimum": 0, "maximum": 21},
                "kcal":         {"type": "integer"},
                "notes":        {"type": "string"},
            },
            "required": ["date", "sport_name", "duration_min"],
        },
    },
    {
        "name": "log_workout_set",
        "description": "Log a single set for a strength workout. If workout_date is provided (instead of workout_id), the set attaches to the most recent workout on that date; if none exists, a placeholder source='manual' workout is created.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workout_id":   {"type": "integer"},
                "workout_date": {"type": "string"},
                "exercise":     {"type": "string"},
                "reps":         {"type": "integer", "minimum": 1, "maximum": 100},
                "weight_lbs":   {"type": "number", "minimum": 0, "maximum": 2000},
                "rpe":          {"type": "number", "minimum": 1, "maximum": 10},
                "notes":        {"type": "string"},
            },
            "required": ["exercise", "reps"],
        },
    },
    {
        "name": "get_recent_meals",
        "description": "Read the user's logged meals for the last N days. Read-only.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 90}},
            "required": ["days"],
        },
    },
])

READ_TOOLS.add("get_recent_meals")
WRITE_TOOLS.update({"log_meal", "log_manual_workout", "log_workout_set"})
```

Handler responsibilities:
- `log_meal` — insert into `meals`, recompute `manual_log` day aggregate for that date.
- `log_manual_workout` — insert into `workouts` (existing table) with `source='manual'`, UUID `source_id`, `raw=NULL`, computed `started_at` from `date` + assumed 00:00 time + duration.
- `log_workout_set` — resolve target workout (by id, OR most-recent on date, OR create placeholder manual workout), compute next `set_number` for that exercise, insert.
- `get_recent_meals` — SELECT from `meals` for last N days, return list of `{date, time, meal_name, kcal, protein_g, fat_g, carbs_g, photo_path?}`.

### 5. Backend — multimodal `/api/chat`

Today `/api/chat` accepts `messages: [{role, content: str}]`. Extend to accept Anthropic-format content arrays:

```python
class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: Union[
        str,
        List[Union[TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock]],
    ]

class ImageBlock(BaseModel):
    type: Literal["image"]
    source: ImageSource

class ImageSource(BaseModel):
    type: Literal["base64"]
    media_type: str   # "image/jpeg", "image/png", etc.
    data: str         # base64-encoded bytes
```

When a chat request contains image blocks, the backend:
1. Decodes the base64 bytes, hashes them (sha256) for dedup, uploads to bucket at `meals/<sha256>.jpg` (only if not already there).
2. Passes the image block through to Anthropic unchanged (Claude vision reads the base64 directly).
3. Appends a system-prompt note: *"The user attached an image in this message; it has been saved to bucket key `meals/<sha256>.jpg`. If you decide to call `log_meal` based on the image content, pass this key as `photo_path` so the meal row references the saved photo."*

Claude then has both the image content (for analysis) AND the bucket key (to thread into the tool call). The user confirming the tool call doesn't re-upload — the photo is already saved.

### 6. Backend — day-aggregate recompute

Helper `_recompute_day_aggregate(session, user_id, date)`:

```python
totals = await session.execute(
    select(
        func.sum(Meal.kcal),
        func.sum(Meal.protein_g),
        func.sum(Meal.fat_g),
        func.sum(Meal.carbs_g),
    ).where(Meal.user_id == user_id, Meal.meal_date == date)
)
kcal, protein, fat, carbs = totals.one()

await session.execute(
    pg_insert(ManualLog)
    .values(user_id=user_id, log_date=date,
            kcal_consumed=kcal, protein_g=protein, fat_g=fat, carbs_g=carbs)
    .on_conflict_do_update(
        index_elements=["user_id", "log_date"],
        set_={"kcal_consumed": kcal, "protein_g": protein,
              "fat_g": fat, "carbs_g": carbs},
    )
)
await session.commit()
```

Called after every meal insert/update/delete. Existing dashboard surfaces (today_strip, log_status, drill-downs) reflect the rollup automatically.

### 7. Frontend — ChatDrawer voice + photo

Two new icon buttons in the input row (before the existing send button):

**Voice (🎤):**
```tsx
const Recognition = (window as any).webkitSpeechRecognition || (window as any).SpeechRecognition;
const supported = !!Recognition;
// Mic icon hidden when !supported (Firefox).
// Click → start; while listening: ring animation; on result → setInput(transcript).
```

**Attach photo (📎):**
```tsx
<input type="file" accept="image/*" hidden ref={fileRef} onChange={onPick} />
// Selected file → readAsDataURL → store as base64 in a `pendingImage` state slot
// Render a 40×40 thumbnail "chip" above the input with an X to remove
// On send: assemble multimodal content array, send to /api/chat
```

`useChatStream.send(text, attachments?)` signature extends to accept optional `attachments: [{type: 'image', mediaType, data}]`. When attachments present, message content is the array form; otherwise plain string.

### 8. Frontend — tool-use confirmation with image preview

`ChatToolUsePrompt.tsx` extends: when `tool.input.photo_path` is set on a `log_meal` call, render an `<img src="/api/meals/preview?photo_path=...">` thumbnail next to the macros list. Same proxy mechanism as `/api/meals/{id}/photo` but works on a path that isn't yet attached to a meal row.

Actually simpler: store the image base64 in the React state when the user picked it. Render the chip from local state. The thumbnail in the tool-use card uses the same in-memory base64 — no extra fetch.

### 9. Frontend — expandable workout rows

`WorkoutTable.tsx` adds a column with a chevron. Click handler:
- Local state: `expanded: Set<workoutId>`
- On expand: fetch `/api/workouts/{id}/sets` (only first time, cache thereafter)
- Render a nested table under the row with: set #, exercise, reps, weight, RPE, notes
- Loading state: skeleton 1-row spinner while fetching

If a workout has zero sets, the expanded view shows "no sets logged".

## Data flow examples

### Meal-with-photo flow

1. User pulls out phone after dinner. Opens dashboard. Click paperclip in ChatDrawer → picks `IMG_1234.jpg`. Types "log this dinner". Sends.
2. Frontend reads file → base64 → assembles `{messages: [{role: "user", content: [{type: "text", text: "log this dinner"}, {type: "image", source: {type: "base64", media_type: "image/jpeg", data: "..."}}]}]}` → POSTs to `/api/chat` (same-origin proxy).
3. Backend decodes base64, sha256-hashes, uploads `meals/<sha>.jpg` to bucket if absent. Builds system prompt with the bucket key. Forwards image+text to Anthropic via `messages.stream(tools=...)`.
4. Claude (vision): "That looks like a chicken stir-fry with rice." Calls `log_meal({date: today, meal_name: "chicken stir fry", kcal: 650, protein_g: 40, fat_g: 25, carbs_g: 65, photo_path: "meals/<sha>.jpg"})`. Backend emits SSE `tool_use` event with these args.
5. Frontend tool-use card shows: thumbnail (from local base64), name, macro estimates, Confirm/Edit/Cancel buttons.
6. User clicks Confirm. Frontend POSTs back to `/api/chat` with `tool_confirmation: {id, approved: true}`. Backend runs `log_meal` handler → inserts meal row → recomputes day aggregate. Resumes Anthropic stream.
7. Claude: "Done — chicken stir fry logged for 6:45 PM, 650 kcal." Streams back. Drawer shows the success message.

### Voice flow

1. User clicks 🎤 in ChatDrawer.
2. Browser asks for mic permission (once); approved.
3. SpeechRecognition starts. User says: "log breakfast three eggs and toast about four hundred calories twenty-five grams protein."
4. SpeechRecognition emits transcript → frontend populates input box.
5. User edits if needed (e.g. swaps "twenty-five" for "25") OR hits send directly.
6. Normal chat flow → Claude → `log_meal({date: today, meal_name: "eggs and toast", kcal: 400, protein_g: 25, ...})` → user confirms → done.

### Mid-workout set logging

1. User between sets at the gym. Opens dashboard chat → "back squat 5 reps 315 RPE 8".
2. Claude: `log_workout_set({workout_date: today, exercise: "back squat", reps: 5, weight_lbs: 315, rpe: 8})`.
3. Backend resolves target workout:
   - SELECT most-recent `workouts` row for user + today.
   - If found: attach set there.
   - If none: insert placeholder `workouts` row (`source='manual'`, `sport_name='strength'`, duration unknown yet, `strain=NULL`), then attach the set.
4. User confirms. Set row inserted. Tool-use card replies "Logged set: back squat 5×315 @ RPE 8 (set 1 of today's strength workout)."
5. Later: user does another set → repeats. The placeholder workout collects sets through the session.
6. Tomorrow morning: Whoop ingests yesterday's strap-recorded workout. Two rows exist: the manual placeholder + the Whoop row. Acceptable for v3; future workflow may de-dupe.

### Manual off-strap workout

1. User did a 30-min easy run, didn't wear Whoop. Chat: "log a 30 min easy run today, maybe strain 5".
2. Claude: `log_manual_workout({date: today, sport_name: "running", duration_min: 30, strain: 5})`.
3. Backend inserts `workouts` row with `source='manual'`, generates `source_id = uuid4()`, sets `started_at` to date 00:00, no `raw`. User confirms. Done.
4. Row appears in the `/workouts` table alongside Whoop-sourced rows, distinguishable by `source='manual'`.

## Error handling

- **Bucket upload fails:** backend returns `{error: "photo storage unavailable"}` SSE event; chat continues without the photo, Claude can still estimate macros from the text description.
- **Vision call fails:** Claude SDK raises; backend emits `error` SSE; user can retry or fall back to text-only description.
- **`log_meal` confirmation arrives after a stale `tool_use_id`:** backend rejects (existing behavior), drawer surfaces "this confirmation expired".
- **Set logged before Whoop ingests workout:** placeholder created (documented above), no error.
- **Mic permission denied:** browser exception caught; voice icon greys out; tooltip "voice unavailable — grant mic permission in browser settings".
- **Large image > 5MB:** frontend rejects before sending with a toast "photo too large; max 5MB please".

## Testing strategy

**Backend:**
- Unit tests for each new handler (5 new tools × happy path + 1 validation-error path = ~10 tests).
- Storage client test: mock boto3, assert upload key is the sha256 of the bytes.
- Day-aggregate recompute test: insert 3 meals, assert `manual_log.kcal_consumed` matches the sum.
- Multimodal chat route test: mock Anthropic stream, send a request with an image block, assert bucket upload happened and image block was forwarded to Anthropic unchanged.
- Set-attach test: log a set with `workout_date: today` when no workouts exist → placeholder created; with one workout exists → set attached to it; with two workouts → set attached to most recent (max started_at).

**Dashboard:**
- Vitest for the multimodal `send` path in `useChatStream`: pass attachments, assert outgoing fetch body has the correct content-array shape.
- Vitest for SpeechRecognition: mock the API, assert transcript populates input.
- One Playwright test: photo upload via the paperclip → tool-use card appears → confirm → meal row exists in DB (or mocked GET /api/meals returns the new row).

**Visual:** Hugo browser-checks after deploy. Hero/tile/workouts visual unchanged; only the chat drawer gains icons and the workouts table gains chevrons.

## Out-of-plan followups (defer to v4)

- **Edit a logged meal** — currently delete + re-log; v4 adds in-place edit via chat.
- **Workout de-dup** — when Whoop later ingests a workout that overlaps a placeholder, surface the conflict in chat or auto-merge.
- **Per-set chart in workout drill-down** — visual progression of weight over time per exercise.
- **Bulk meal log** — "I had X, Y, Z today" creating 3 meal rows in one tool call.
- **Persistent chat history** — still session-only in v3.
- **Mobile responsive** — capture works on mobile in landscape; phone-portrait polish deferred.
- **Object storage cleanup** — orphaned photos (uploaded then user cancels) accumulate. Daily cron to GC photos without an attached meal row.

## Task list (handoff to writing-plans)

| # | Task | Repo |
|---|---|---|
| T1 | Backend: alembic migration for `meals` + `workout_sets` tables | service |
| T2 | Backend: `storage.py` — boto3 wrapper + bucket env config + tests | service |
| T3 | Backend: `routes/meals.py` — CRUD + photo upload/stream + day-aggregate recompute + tests | service |
| T4 | Backend: `routes/workout_sets.py` — CRUD + tests | service |
| T5 | Backend: `routes/workouts_manual.py` — manual workout create + tests | service |
| T6 | Backend: extend `chat_tools.py` — `log_meal`, `log_manual_workout`, `log_workout_set`, `get_recent_meals` + tests | service |
| T7 | Backend: `/api/chat` multimodal — image block parsing + bucket upload + system-prompt threading + tests | service |
| T8 | Railway infra: provision Object Storage bucket + set env vars (`S3_*`) on backend service | infra |
| T9 | Dashboard: ChatDrawer mic button + Web Speech wiring + feature-detect gate | dashboard |
| T10 | Dashboard: ChatDrawer paperclip + file picker + base64 reader + image preview chip + multimodal `useChatStream.send` | dashboard |
| T11 | Dashboard: `ChatToolUsePrompt` renders photo thumbnail when present (uses in-memory base64 from sender, not refetched) | dashboard |
| T12 | Dashboard: `WorkoutTable` chevron + expandable sub-table + fetch sets on demand | dashboard |
| T13 | Backend + dashboard: end-to-end smoke — voice → text, photo → meal log, manual workout, mid-workout set log | both |
| T14 | Tag releases: backend `v0.5.0-capture`, dashboard `v0.3.0-capture` | both |

**Estimated wall time:** ~3-5 focused days (~14 tasks).

## Decisions captured during brainstorming (2026-05-19)

1. **All four capture features in one v3** (vs splitting voice+manual into a fast v3a and meals/sets into v3b). Hugo wanted them shipped together.
2. **Image storage = Railway Object Storage** (S3-compatible). Photos uploaded to `meals/<sha256>.jpg`; DB stores keys only; reads stream through a backend proxy. Not Postgres bytea, not skip-photos-entirely.
3. **Voice support = Chrome/Safari/Edge only via Web Speech API**. Mic icon hidden gracefully on Firefox. No server-side transcription fallback (not worth the complexity for a single-user tool).
4. **Per-set UI = expandable rows on `/workouts`** (vs new drill-down page). Click a chevron, sub-table appears under the row.
5. **Photo workflow = Claude vision auto-estimates macros**, user confirms or edits via the existing tool-use confirmation card (vs manual entry with photo as just-a-record).
6. **Day aggregate recompute = automatic on every meal write**. `log_meal` (and any delete/update) updates `manual_log.kcal_consumed / protein_g / fat_g / carbs_g` for that day to the sum of `meals` rows. Single source of truth: meals. The manual_log day-level fields stay populated for backwards compatibility with dashboard surfaces.
7. **Set placement = most-recent-workout-or-create-placeholder**. If a set is logged with `workout_date: today` and no workouts row exists for today, a placeholder `source='manual'` workouts row is created and the set attaches there. If Whoop later ingests the strap-recorded workout, two rows coexist (placeholder + Whoop); v3 accepts this; future workflow may dedupe.
