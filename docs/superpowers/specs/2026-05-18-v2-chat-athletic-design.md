# v2 — Chat Drawer + Athletic Visual Polish: Design Spec

**Date:** 2026-05-18
**Status:** Approved (architecture); plan to follow
**Repos affected:** `growthink1/health-metrics-service` + `growthink1/health-metrics-dashboard`
**Predecessor specs:** [`2026-05-14-health-metrics-dashboard-design.md`](2026-05-14-health-metrics-dashboard-design.md) (Plan 2), [`2026-05-18-railway-deploy-design.md`](2026-05-18-railway-deploy-design.md) (Plan 3)

## Goal

Make the deployed dashboard at `https://health.ironforgeai.com` feel like a polished athletic product (Whoop / fitness-app aesthetic) and add a conversational layer where Claude can both answer questions about the data and log manual entries on Hugo's behalf via tool-use.

## Scope

In:
- **Right-side persistent chat drawer** (collapsible to a 32 px rail). Q&A + manual-log writes via Anthropic tool-use, streaming token-by-token via SSE.
- **Visual redesign to Whoop/Athletic aesthetic** — hero recovery ring + uppercase recommendation replaces the existing 4-cell TodayStrip; color-coded KPI strip; restyled sparkline tiles; athletic typography (Inter for body, JetBrains Mono for metadata only).
- **Tool-use confirmation flow** — every write proposed by Claude renders an inline Yes/No prompt in the chat thread; user must approve before the upsert lands.
- **Claude Design output integration** — Hugo runs an external Claude Design prompt (specified verbatim below), brings back HTML/CSS, we port into Tailwind v4 components.

Out (deferred to v3 or later):
- Persisting chat history across sessions. Chat is session-only on v2.
- Voice input / mic.
- Multi-modal image upload (e.g. photograph a meal to log macros).
- Compare-period view (this-week vs last-week overlays).
- Workout-marker overlays on HRV/sleep drill-down charts.
- Export CSV / PDF.
- Mobile-first responsive layout.

## Non-goals

- Multi-user. Still single-user (Hugo), Basic Auth gated.
- A separate Claude project / agent SDK. v2 uses the standard Anthropic `messages` API with tool-use.
- Changing the backend's existing 5 dashboard endpoints. Those keep working as-is; v2 adds one new endpoint (`/api/chat`).

## Architecture

```
┌─ health.ironforgeai.com (Basic Auth, single-user) ─────────────────────────────┐
│                                                                                │
│  Left column (flex 2)                          Right drawer (~360 px, flex 0)  │
│  ┌──────────────────────────────────────┐     ┌────────────────────────────┐  │
│  │  Hero: recovery ring + DELOAD + sub  │     │   ┌─ user ──┐              │  │
│  │  ──────────────────────────────────  │     │   │ why so  │              │  │
│  │  KPI strip: HRV / RHR / Sleep / Stra │     │   │ low?    │              │  │
│  │  ──────────────────────────────────  │     │   └─────────┘              │  │
│  │  Narration line                       │     │              ┌─ claude ─┐ │  │
│  │  Log panel (subjective inputs)        │     │              │ HRV ...  │ │  │
│  │  ──────────────────────────────────  │     │              │ ▮▮▮▯     │ │  │
│  │  Window selector                      │     │              └──────────┘ │  │
│  │  6-tile sparkline grid (Whoop style)  │     │                            │  │
│  │                                        │     │   ┌─ tool-use prompt ──┐  │  │
│  │                                        │     │   │ log weight=218     │  │  │
│  │                                        │     │   │ for 2026-05-18     │  │  │
│  │                                        │     │   │ [Confirm] [Cancel] │  │  │
│  │                                        │     │   └────────────────────┘  │  │
│  │                                        │     │                            │  │
│  │                                        │     │   ────────────────────    │  │
│  │                                        │     │   [ Ask Claude...  ▶ ]    │  │
│  └──────────────────────────────────────┘     └────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────┘
```

The chat drawer is a sibling of `<main>` directly inside `<body>`. The `<body>` becomes a horizontal flex container (`flex min-h-screen`), `<main>` flexes to fill, the drawer is fixed-width on the right. This keeps the drawer at full viewport height regardless of how tall `<main>` content gets, and avoids inheriting `<main>`'s existing `max-w-5xl` page-width constraint. The drawer collapses to a 32 px rail (vertical "ASK CLAUDE" text); clicking expands it back. Drawer expand/collapse state is persisted in `localStorage`; chat messages are React state only (session-scoped, lost on reload — v3 carryover).

All chat HTTP traffic goes via the existing same-origin proxy at `/api/[...path]`:
- Browser → `/api/chat` → dashboard Next.js → catchall proxy → backend internal → Anthropic API
- Streaming pass-through: backend receives Anthropic SSE → forwards to dashboard → dashboard forwards to browser unchanged
- Tool-use mid-stream pauses streaming, returns a confirmation token to the browser, awaits POST back

## Components

### 1. Backend: `POST /api/chat` (`health-metrics-service`)

New file `src/health_metrics/routes/chat.py`. Single endpoint, SSE response. Body shape:

```python
class ChatRequest(BaseModel):
    user_id: str | None = None
    messages: list[ChatMessage]  # OpenAI-style {role, content} but content may be a list for tool results
    tool_confirmation: ToolConfirmation | None = None  # set when client responds to a tool prompt

class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str | list[dict]
```

Behavior:
1. Validate request. Resolve `user_id`.
2. Build system prompt by reading last 30 d of regulation signals via `compute_regulation_signals` + today's `regulate()` recommendation + recent workouts. Bake into the system message so Claude has full context.
3. Define tools (see § 2) with `anthropic.types.ToolParam` schemas.
4. Call `client.messages.stream(model=settings.narration_model, system=..., tools=..., messages=...)`.
5. Iterate the stream:
   - `text_delta` → forward as SSE `data: {"type":"text","delta":"..."}`
   - `tool_use` (input_json complete) → pause the underlying Anthropic stream, emit SSE `data: {"type":"tool_use","id":"...","name":"...","input":{...}}`, return — client decides yes/no.
6. When client POSTs back with `tool_confirmation: {id, approved, result?}`:
   - If `approved=false`: append a synthetic tool_result `"User declined."` and resume the messages stream by re-invoking with the appended history.
   - If `approved=true`: execute the tool handler server-side, capture the result dict, append as a `tool_result` content block, resume.
7. Stream ends with `data: {"type":"done"}`.

Single new endpoint; ~150 lines. New file means it has one clear responsibility (chat lifecycle).

### 2. Backend: tools (`src/health_metrics/chat_tools.py`)

New module. Defines 5 tools as Anthropic-format dicts + their handler functions:

| Tool | Args | Behavior |
|---|---|---|
| `get_recent_metrics(days: int = 30)` | `days` 1-90 | Reads `daily_metrics` for user, returns JSON of dates + key fields. |
| `get_workouts(days: int = 30)` | `days` 1-90 | Reads `workouts`, returns JSON list. |
| `log_subjective(date, energy?, mood?, hunger?)` | ISO date + optional 1-10 ints | Upserts `manual_log` row — same path as the existing `LogPanel` form. |
| `log_weight(date, weight_lbs)` | ISO date + float | Upserts `manual_log.weight_lbs`. |
| `log_nutrition(date, kcal?, protein_g?, fat_g?, carbs_g?)` | ISO date + optional ints | Upserts macro fields. |

Each handler returns `{ok: True, result: {...}}` or `{ok: False, error: "..."}`. Read tools execute immediately; write tools never execute server-side without a `tool_confirmation: approved=True` from the client.

Module owns its responsibility cleanly: tool definitions in one place, handler implementations in one place, no chat-state coupling.

### 3. Backend: system prompt builder (`src/health_metrics/chat_prompts.py`)

New file. One function:

```python
async def build_system_prompt(session, user_id) -> str
```

Reads last 30 d of `daily_metrics` + most recent regulation signals, formats as:

```
You are a recovery + training-readiness coach for Hugo. Today's data:
- Recommendation: DELOAD ("Volume -30%, Z2 only, extra rest day")
- Recovery: 6 (very low)
- HRV today: 15ms (1.8σ below baseline)
- ...

Recent 30 days of metrics (compact JSON):
[{date: "2026-04-19", hrv: 24.2, rhr: 65, ...}, ...]

You may answer questions about this data, suggest interpretation, or use tools to log
new manual entries when the user asks ('log my weight at 218', 'energy was 7 today').
Always confirm writes with the user before executing.
```

Keeps the system prompt construction isolated so it's easy to iterate on tone/specificity without touching the streaming loop.

### 4. Dashboard: `lib/chat.ts`

New file. Single hook:

```ts
useChatStream(): {
  messages: ChatMessage[];
  pendingToolUse: ToolUsePrompt | null;
  isStreaming: boolean;
  send: (text: string) => void;
  confirmToolUse: (approved: boolean) => void;
  cancel: () => void;
}
```

Internally manages an `AbortController` over a `fetch` POST to `/api/chat` with `Accept: text/event-stream`. Parses SSE chunks, accumulates the streaming assistant text into the last message, surfaces `tool_use` events as `pendingToolUse`, posts a second request to resume when the user confirms.

### 5. Dashboard: `components/ChatDrawer.tsx` (new)

Renders the drawer UI:
- Toggle handle (vertical "ASK CLAUDE" rail when collapsed; X icon when expanded)
- Scrolling message list with user/assistant bubbles, streaming cursor on the in-flight message
- Tool-use confirmation card inline in the thread when `pendingToolUse` is set
- Input field at the bottom with send button (Enter to send)

Uses `useChatStream`. Drawer width + collapsed state persisted to `localStorage` key `chat:drawer:state`.

### 6. Dashboard: `components/HeroBlock.tsx` (new, replaces TodayStrip)

Composes:
- SVG recovery ring (left, ~140 px diameter, score color = red <33 / amber 33–66 / green >66)
- Uppercase recommendation (right of ring, large athletic typography)
- Training-mod subline
- Narration line beneath (italic, accent left-border)

Reads `today.today_strip` from the existing `/api/dashboard/today` response. No backend change.

### 7. Dashboard: `components/MetricChip.tsx` (new)

Thin horizontal chip strip — HRV / RHR / Sleep / Strain — each chip a color-coded compact card: label, value, optional delta. Hover state highlights. Below the hero, above the grid.

### 8. Dashboard: restyle existing components

- `SparklineTile.tsx`: top accent stripe per metric color, larger hero number, hover lift.
- `MetricChart.tsx` (drill-down): athletic-style axis labels in JetBrains Mono, color-per-metric line stroke, retain ±1σ band.
- `WorkoutTable.tsx`: row hover, color-coded strain column.

These stay structurally the same; only Tailwind classes change to match the Claude Design output.

## Data flow examples

**Flow A — Q&A "why is recovery so low today?"**
1. User types in drawer input, hits Enter.
2. `useChatStream.send(text)` adds a user message, POSTs `/api/chat` with the message history.
3. Same-origin proxy forwards to backend internal URL.
4. Backend builds system prompt with today's signals + 30 d data, calls Anthropic streaming.
5. Anthropic streams text deltas → backend forwards as SSE → dashboard parses → drawer's last (in-flight) assistant message accumulates tokens.
6. Stream ends; final message persisted to component state. Done.

**Flow B — Action "log my weight at 218"**
1. User types, sends.
2. Backend streams; Anthropic decides to call `log_weight(date="2026-05-18", weight_lbs=218)`.
3. Backend pauses, emits `data: {"type":"tool_use","id":"toolu_...","name":"log_weight","input":{"date":"2026-05-18","weight_lbs":218}}`, ends the SSE response.
4. Dashboard shows the tool-use card inline: *"I'll log weight=218 for 2026-05-18 — confirm?"* with Confirm/Cancel.
5. User clicks Confirm → `useChatStream.confirmToolUse(true)` → second POST to `/api/chat` with `tool_confirmation: {id, approved: true}` plus the original message history.
6. Backend executes the `log_weight` handler (does the DB upsert), appends the tool_result to messages, resumes the Anthropic stream.
7. Claude continues: "Done — weight logged for today." Streamed back to drawer.

**Flow C — Drawer collapse**
1. User clicks the toggle.
2. Drawer width animates `360px → 32px` over 200 ms via Tailwind transitions.
3. `localStorage.setItem("chat:drawer:state", "collapsed")`.
4. On reload, drawer initializes in the persisted state.

## Error handling

- **Anthropic API 5xx or timeout:** backend catches, emits `data: {"type":"error","message":"Claude is offline"}`, ends stream. Drawer shows the error inline with a retry button.
- **Tool handler raises:** backend emits `data: {"type":"tool_error","id":"...","message":"..."}`. Drawer shows the error in the tool-use card, the chat is still alive, user can ask Claude to try differently.
- **User aborts mid-stream:** `useChatStream.cancel()` calls `AbortController.abort()`. Backend sees client disconnect, cancels the Anthropic stream. No partial DB writes since writes only happen after the client confirms.
- **Confirmation request with stale tool_use_id:** backend rejects with 400. Drawer surfaces a "this request expired" toast.
- **DB unavailable for a write:** handler returns `{ok: False, error: "..."}`, surfaced same as tool_handler raise.

## Testing strategy

**Backend:**
- Unit tests for each tool handler (5 tools × happy path + 1 error path each = 10 tests). Use the `test_user_id` fixture for isolation.
- Mock Anthropic streaming with a fake `AsyncIterator` that yields scripted events; assert the SSE output matches expected JSON lines (one test per flow A, B, C).
- The system_prompt_builder gets a snapshot test: given a fixture DB state, the produced prompt matches an expected string (modulo timestamp).

**Dashboard:**
- Vitest for `useChatStream`: mock `fetch` returning a `Response` with a streaming body; assert that messages accumulate correctly, tool_use events transition state, confirmation triggers a second fetch.
- One Playwright e2e:
  1. Open dashboard.
  2. Send "what's my recovery today?" via chat.
  3. Assert response text contains "recovery" (loose).
  4. Send "log my weight at 218".
  5. Assert confirmation card appears.
  6. Click Confirm.
  7. Assert `/api/dashboard/today` shows weight in log_status (or query the DB directly).

**Visual:** Hugo browser-checks after deploy. Whoop/Athletic redesign is judgment-based, not automated.

## Claude Design prompt (Hugo runs externally)

Hugo pastes the following into Claude Design (claude.ai/design or the standalone Design assistant), runs it, brings back the HTML/CSS output. We extract the styles and port into Next.js + Tailwind v4 components.

> Design a single dark-mode dashboard page for a personal health analytics app called health-metrics. It's a single-user tool that ingests Whoop + Oura data and shows daily recovery / training-readiness.
>
> Visual direction: WHOOP / ATHLETIC. Think: Whoop app dark mode meets a refined fitness tracker.
>
> - Dark background (#0a0e14 or near-black)
> - Bold athletic uppercase typography for the recommendation (e.g. "DELOAD", "MAINTENANCE")
> - Color-coded metrics: recovery = red→amber→green by score, HRV = cool blue, RHR = warm amber, Strain = magenta/pink, Sleep = teal/green
> - Hero element: a circular recovery ring (SVG arc, 120-160px diameter) with the score inside, next to the uppercase recommendation
> - KPI strip below hero: 4-5 thin horizontal chips, each a metric + value, color-coded
> - 6-tile sparkline grid below the strip: HRV, RHR, SLEEP, STRAIN, RECOVERY, WEIGHT. Each tile is ~200px wide, has a color-per-metric top accent stripe, the current value as a hero number, and a tiny sparkline chart underneath.
> - Narration block: italic single sentence, left-border accent (Claude-generated copy like "Your HRV dropped 1.75 SD below baseline — classic overtraining signal").
> - Manual log panel: inline form with 3 small number inputs (energy, mood, hunger 1-10), one "more" expand button (weight, kcal, macros), and a Save button. Keep it compact.
>
> Right-side: a chat drawer (persistent, 360px wide, collapsible to a thin 32px rail with vertical "ASK CLAUDE" text). Inside the drawer:
>
> - Chat history (bubbles for user + assistant)
> - A "claude wants to log: weight=218 for 2026-05-18 [Confirm] [Cancel]" tool-use prompt UI shown inline in the chat thread
> - Input field at the bottom with send button
> - Subtle animation when tokens are streaming in
>
> Typography:
>
> - Inter for body / numbers
> - JetBrains Mono for dates, axis labels, log status
> - Uppercase + tracking for labels
>
> Constraints:
>
> - All in one screen, no scroll for the hero+KPI strip on a 1440x900 viewport
> - Dark mode only
> - Existing CSS variables we'd like to reuse if possible: `--bg #0a0e14`, `--surface #0e1422`, `--border #1f3a5f`, `--accent-primary #7eb5e0`, `--accent-warm #e2a04a`, `--accent-good #5ad4a8`, `--accent-bad #e25a4a`, `--accent-strain #d44a8a`
>
> Real sample data to populate the mockup:
>
> - Recommendation: DELOAD
> - Sub-line: "Volume −30%, Z2 only, extra rest day"
> - Recovery score: 6 (RED — very low)
> - HRV today: 15ms (1.8σ below baseline)
> - RHR today: 81 bpm (1.8σ above baseline)
> - Sleep last night: 7.4h
> - Strain today: 5.8
> - Narration: "Your HRV dropped 1.75 SD below baseline while resting heart rate spiked 1.84 SD above normal—classic overtraining signals requiring recovery."
>
> Deliver: a single HTML file with inline CSS (Tailwind-compatible class names where possible so we can port to our Next.js + Tailwind setup). Include the chat drawer in the same document showing one user message and one streaming assistant reply with a tool-use confirmation UI.

## Task list (handoff to writing-plans)

| # | Task | Repo | Est |
|---|---|---|---|
| T1 | Backend: `chat_tools.py` — tool registry + 5 handlers + unit tests | service | 90m |
| T2 | Backend: `chat_prompts.py` — system prompt builder + snapshot test | service | 30m |
| T3 | Backend: `routes/chat.py` — SSE streaming endpoint + tool_use pause/resume | service | 90m |
| T4 | Backend: register chat router in `main.py` + smoke locally | service | 15m |
| T5 | Dashboard: integrate Claude Design HTML — extract palette + class names into Tailwind v4 `@theme` + component-level Tailwind classes | dashboard | 2h |
| T6 | Dashboard: `components/HeroBlock.tsx` (replaces TodayStrip) + `components/MetricChip.tsx` | dashboard | 90m |
| T7 | Dashboard: restyle `SparklineTile`, `MetricChart`, `WorkoutTable` to match | dashboard | 60m |
| T8 | Dashboard: `lib/chat.ts` SSE hook + Vitest | dashboard | 90m |
| T9 | Dashboard: `components/ChatDrawer.tsx` + tool-use confirmation UI + localStorage state | dashboard | 90m |
| T10 | Dashboard: mount ChatDrawer in `app/layout.tsx` adjacent to `<main>` | dashboard | 20m |
| T11 | Playwright e2e: chat Q&A + log-weight-via-chat with confirm | dashboard | 45m |
| T12 | End-to-end smoke at `https://health.ironforgeai.com` | infra | 20m |
| T13 | Tag releases: backend `v0.4.0-chat`, dashboard `v0.2.0-athletic-chat` | both | 15m |

**Estimated wall time:** ~10-11 h focused work + Hugo's external Claude Design run (~15 min) + the spec/plan/implementation review loop.

## What's NOT in this plan (deferred to v3 or later)

- Chat history persistence (DB table for messages; chat survives page reloads with full thread).
- Voice input (mic icon → Web Speech API → transcribe → send).
- Multi-modal image upload (photograph a meal, log macros).
- Compare-period view, workout overlays, export, mobile-first responsive — all carry from Plan 2's deferred list.
- A separate `audit_log` table for tool-use writes (we'll know via DB writes themselves; no separate audit needed at this scale).
- Tool: `delete_manual_log` (intentional omission — destructive deletes don't go through chat).
