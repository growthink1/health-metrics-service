# v4 — Goals, Projections, and Recommendations: Design Spec

**Date:** 2026-05-20
**Status:** Approved (design); plan to follow
**Repos affected:** `growthink1/health-metrics-service` + `growthink1/health-metrics-dashboard`
**Predecessor specs:**
- `2026-05-14-health-metrics-dashboard-design.md` (Plan 2)
- `2026-05-18-railway-deploy-design.md` (Plan 3)
- `2026-05-18-v2-chat-athletic-design.md` (v2 chat + athletic)
- `2026-05-19-v3-capture-design.md` (v3 day-of-life capture)

## Goal

Turn the dashboard from a passive analytics view into an active coach: track one primary goal at a time, project where the user will land at deadline with Bayesian confidence intervals, surface auto-computed milestones + user-defined subgoals, and produce daily recommendations that compose with the existing regulation engine. Claude chat can on-demand fetch best-practice context from the web via Anthropic's `web_search` tool.

## Scope

In:
- **Four goal types** — weight, strength PR, habit consistency, recovery/HRV.
- **One primary goal at a time** — auto-archive prior primary when a new one is set. Subgoals + milestones hang off it.
- **Bayesian projection** — closed-form conjugate updates (Normal-Normal for continuous, Beta-Binomial for habit) using scipy. Posterior mean, 95% CI, and `p_on_pace` per goal per day.
- **Auto-computed milestones** — monthly checkpoints linearly interpolated between start and target.
- **Typed-preset subgoals** with live compliance from existing tables.
- **Daily background recompute** — APScheduler runs after metric ingestion. Action composition is rules-based, deterministic. One LLM (haiku) call per goal per day for the narration; content-addressed cache via `signals_hash`.
- **On-demand chat recommendations** — `get_goal_status` tool returns the same snapshot the UI reads; chat (sonnet) reasons over it with Anthropic's `web_search` available.
- **Goal lifecycle** managed entirely through chat: brainstorm-style interview for first-time setup, `set_primary_goal` / `add_subgoal` / `update_goal` tools for edits.
- **`/goals` page (Layout A — Hero Focus)** — big trajectory chart with CI ribbon, three-stat row, milestone + subgoal columns, recommendation card.

Out (deferred to v5+):
- Multiple parallel primary goals.
- Goal templates / picker UI (replaced by the brainstorm interview).
- Adherence tracking ("did you follow this recommendation?" boolean log).
- Cross-goal trade-off reasoning (e.g., recomp deficit vs. strength gain conflict).
- Mobile-responsive `/goals` layout — explicitly removed from v4.
- Auto outlier detection on weight/measurements.
- Email/push notifications when behind on milestones.
- Multi-user / shared goals.
- Weekly/monthly PDF reports.
- Auto-generated subgoals from `/goals` UI (the brainstorm interview handles this).
- Goal context on the today strip (`/`) — kept "today's data" focused.
- Web research caching / curated source allowlist beyond Anthropic's `web_search` defaults.
- Editing past `goal_recommendations` rows — stale rows are immutable history.
- Online / streaming Bayesian updates — daily batch is sufficient.

## Non-goals

- Multi-user. Still single-user (Hugo), HTTP Basic Auth gated.
- Real-time goal updates (e.g., notifying mid-day when a milestone hits). Daily recompute is the cadence.
- Coaching outside the four goal types. If the user wants to track "drink 3L water/day" — not supported in v4.

## Architecture

```
        Browser
        ┌───────────────────────────────────────────────────────────┐
        │  Dashboard (Next.js)                                      │
        │  ┌──────────────────────────┬──────────────────────────┐  │
        │  │ /goals                   │ ChatDrawer               │  │
        │  │  ┌─ GoalHeader ─────────┐│                          │  │
        │  │  │ Lose 15 lbs · Aug 1  ││  brainstorm interview    │  │
        │  │  └──────────────────────┘│  + on-demand chat        │  │
        │  │  ┌─ TrajectoryChart ────┐│  + web_search tool       │  │
        │  │  │   CI ribbon + line   ││                          │  │
        │  │  └──────────────────────┘│                          │  │
        │  │  ┌── 3 stat cards ──────┐│                          │  │
        │  │  │ progress · days · proj                            │  │
        │  │  └──────────────────────┘│                          │  │
        │  │  ┌─Milestones─┬─Subgoals┐│                          │  │
        │  │  └────────────┴─────────┘│                          │  │
        │  │  ┌─ RecommendationCard ─┐│                          │  │
        │  │  │ narration + actions  ││                          │  │
        │  │  │ + "Ask Claude" link  ││                          │  │
        │  │  └──────────────────────┘│                          │  │
        │  └──────────────────────────┴──────────────────────────┘  │
        └───────────────────────────────────────────────────────────┘
                          │
                          ▼ same-origin proxy
        ┌────────────────────────────────────────────────────────────┐
        │  Backend (FastAPI)                                         │
        │   GET  /api/goals/status      ← UI + chat tool             │
        │   GET  /api/goals/history     ← "Recent recs" expand       │
        │                                                            │
        │  New chat tools:                                           │
        │    set_primary_goal · add_subgoal · update_goal            │
        │    get_goal_status                                         │
        │    + Anthropic web_search_20250305 (server-side)           │
        │                                                            │
        │  jobs/projection.py    — Bayesian models                   │
        │  jobs/daily_goals.py   — per-goal daily recompute          │
        │  routes/goals.py       — REST endpoints                    │
        └───────────────────┬────────────────────────────────────────┘
                            │
                            ▼
                   Railway Postgres
        (goals · milestones · subgoals · goal_recommendations
         + existing v3 tables read-only for projections)
```

**Composition with the existing regulation engine.** The regulation engine decides whether the user *can* run a deficit *today* based on recovery / sleep debt / strain. The new goal engine decides whether the deficit they're running is *enough* to hit the target. They compose: regulation produces a veto (deload), goal produces a target (kcal delta).

## Components

### 1. Backend — new tables (alembic migration)

```sql
CREATE TABLE goals (
  id              BIGSERIAL PRIMARY KEY,
  user_id         TEXT NOT NULL,
  goal_type       TEXT NOT NULL,             -- 'weight' | 'strength' | 'habit' | 'recovery_hrv'
  name            TEXT NOT NULL,             -- "Lose 15 lbs"
  metric          TEXT NOT NULL,             -- 'weight_lbs' | 'squat_5rm' | 'workouts_per_week' | 'hrv_avg_30d'
  metric_params   JSONB,                     -- per-type config: { "exercise": "back squat", "reps": 5 }
  start_value     NUMERIC,                   -- snapshot at goal creation; NULL allowed if no data yet
  target_value    NUMERIC NOT NULL,
  start_date      DATE NOT NULL,             -- usually = today at creation
  target_date     DATE NOT NULL,
  is_primary      BOOLEAN NOT NULL DEFAULT TRUE,
  status          TEXT NOT NULL DEFAULT 'active', -- 'active' | 'achieved' | 'archived' | 'missed'
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_goals_user_active ON goals(user_id) WHERE status='active';

CREATE TABLE milestones (
  id              BIGSERIAL PRIMARY KEY,
  goal_id         BIGINT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
  target_value    NUMERIC NOT NULL,
  target_date     DATE NOT NULL,
  hit_at          TIMESTAMPTZ,
  hit_value       NUMERIC
);
CREATE INDEX idx_milestones_goal ON milestones(goal_id);

CREATE TABLE subgoals (
  id              BIGSERIAL PRIMARY KEY,
  goal_id         BIGINT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
  preset          TEXT NOT NULL,             -- 'avg_kcal' | 'workouts_per_week' | 'sleep_hours_avg' | 'protein_g_avg' | 'meal_logs_per_week'
  target_value    NUMERIC NOT NULL,
  window_days     INTEGER NOT NULL DEFAULT 7
);

CREATE TABLE goal_recommendations (
  id              BIGSERIAL PRIMARY KEY,
  goal_id         BIGINT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
  rec_date        DATE NOT NULL,
  trajectory      JSONB NOT NULL,
  actions         JSONB NOT NULL,
  narration       TEXT NOT NULL,
  signals_hash    TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (goal_id, rec_date)
);
```

Migration is purely additive; no existing tables touched. Existing users land on the `EmptyGoalState` first time they visit `/goals`. No rollback risk.

**Subgoal compliance is computed live** from existing data:

| preset | data source | formula |
|---|---|---|
| `avg_kcal` | `manual_log.kcal_consumed` | `100 - abs(avg - target) / target * 100` clipped 0–100, rolling N days |
| `workouts_per_week` | `workouts` count | `min(count(last 7d) / target, 1.0) * 100` |
| `sleep_hours_avg` | `daily_metrics.oura_sleep_duration_min / 60` (Whoop fallback) | same as `avg_kcal` |
| `protein_g_avg` | `manual_log.protein_g` | same |
| `meal_logs_per_week` | `meals` count | `min(count(last 7d) / target, 1.0) * 100` |

### 2. Backend — Bayesian projection module

New module `src/health_metrics/jobs/projection.py`. Pure functions, one per goal type. No `pymc` — conjugate updates are closed-form. Adds `scipy>=1.11` to `requirements.txt`.

**Models:**

| goal_type | model | prior | data window | min observations |
|---|---|---|---|---|
| `weight` | Normal-Normal on weekly slope | μ₀ = −0.5 lb/wk (loss) or +0.25 lb/wk (gain), σ₀ = 0.5 | last 30d of `manual_log.weight_lbs` | 7 |
| `strength` | Normal-Normal on % gain per week | μ₀ = 1% / wk, σ₀ = 0.75% | last 60d of best `weight_lbs` at target reps | 4 PR observations |
| `habit` (workouts/week) | Beta-Binomial on P(workout day) | Beta(α₀=4, β₀=3) | last 28d Bernoulli trials | 7 days |
| `recovery_hrv` | Normal-Normal on weekly Δ HRV | μ₀ = +0.5 ms/wk, σ₀ = 1.5 | last 60d of 7-day rolling avg HRV | 30 |

**Output schema** (embedded in `goal_recommendations.trajectory`):

```python
{
  "method": "bayesian_normal_normal",        # or "beta_binomial" for habit
  "current_value": 192.7,
  "projected_value_mean": 187.2,
  "projected_value_ci_low": 184.1,            # 95% CI
  "projected_value_ci_high": 190.3,
  "p_on_pace": 0.34,                          # 34% chance of hitting target
  "confidence": "high",                       # "high" if CI width < 20% of gap, "med" if 20-50%, "low" if >50% or data_points < 1.5x min_required
  "data_points_used": 28,
  "posterior_params": { "slope_mean": -0.31, "slope_std": 0.08 }
}
```

When `data_points_used < min_observations`:

```python
{ "method": "insufficient_data", "current_value": <whatever's available>,
  "data_points_used": N, "min_required": M, "projected_value_mean": None,
  "projected_value_ci_low": None, "projected_value_ci_high": None,
  "p_on_pace": None, "confidence": "low" }
```

Numerical failures (`np.linalg.LinAlgError`, `ValueError`) are caught and fall back to the `insufficient_data` shape with a logged warning.

### 3. Backend — daily recompute job

New module `src/health_metrics/jobs/daily_goals.py`. Registered with the existing APScheduler in `jobs/scheduler.py`. Fires ~09:00 ET, after the daily metric ingest. Per active goal:

```python
async def daily_goal_recompute(goal: Goal):
    current = compute_current_value(goal)              # per goal_type
    projection = project_to_deadline(goal, current)    # from projection.py
    update_milestones(goal, current)                   # flip hit_at on milestones reached
    regulation = regulate(compute_regulation_signals(...))
    compliances = compute_all_subgoal_compliances(goal)
    actions = compose_actions(goal, current, projection, regulation, compliances)
    signals_hash = hash_inputs(goal.id, current, p_on_pace_bucket(projection),
                               compliance_round(compliances), regulation.rec_type)
    cached = SELECT narration FROM goal_recommendations
             WHERE goal_id=goal.id AND signals_hash=signals_hash
    narration = cached.narration if cached else await claude_narrate(...)
    upsert_goal_recommendation(...)
```

**Action composition (deterministic, rules-based — no LLM here):**

- Threshold `p_on_pace ≥ 0.65` → on track; no nutrition/training change suggested.
- `0.35 ≤ p_on_pace < 0.65` → uncertain; soft nudge ("consider tightening adherence").
- `p_on_pace < 0.35` → off track; compute the deficit/training delta needed to lift `p_on_pace` back to 0.65 via short simulation (~100 iterations).
- Subgoal compliance < 70% → flag as compliance action.
- Regulation engine veto: if `rec_type == "deload"`, drop all training actions, append a recovery action.
- Cap at 5 actions per recommendation.

**Job failure isolation:** each `goal × per-goal-recompute` is wrapped in try/except. One failure doesn't block others. After 3 consecutive failures for the same goal, the `/goals` UI shows a banner: "Recommendation not updated since YYYY-MM-DD."

### 4. Backend — new REST routes

`src/health_metrics/routes/goals.py`:

```python
@router.get("/goals/status")
async def goal_status(user_id: str | None = Query(default=None)) -> dict:
    # Returns the GoalStatus shape (see below). Same body as get_goal_status chat tool.

@router.get("/goals/history")
async def goal_history(user_id: str | None = Query(default=None), days: int = 7) -> dict:
    # Returns last N daily goal_recommendations rows for the "Recent recommendations" expand.
```

Both wrap `_session_factory()` for test injection (existing pattern from `routes/api.py`).

**Response shape (`GoalStatus`):**

```python
{
    "goal": { "id": 1, "name": "Lose 15 lbs", "goal_type": "weight",
              "metric": "weight_lbs", "metric_params": null,
              "start_value": 200, "target_value": 185,
              "start_date": "2026-05-20", "target_date": "2026-08-01",
              "status": "active" }            # or None when no active goal
    "trajectory": { ... },                    # or None
    "milestones": [ { ... }, ... ],           # empty list when no goal
    "subgoals": [ { "preset": "avg_kcal", "target_value": 2100, "window_days": 7,
                    "current_value": 2070, "compliance_pct": 94 }, ... ],
    "recommendation": { "rec_date": "2026-05-20",
                        "narration": "...",
                        "actions": [ { "category": "nutrition", "change": "...", "rationale": "..." }, ... ] }
}
```

### 5. Backend — chat tool additions

Four new tools in `chat_tools.py` + one Anthropic server-side tool declaration.

```python
TOOL_DEFINITIONS.extend([
    {
        "name": "set_primary_goal",
        "description": "Create the user's new primary goal. Automatically archives any existing active primary goal and auto-generates monthly milestones from start to target_date. User MUST confirm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_type":    {"type": "string", "enum": ["weight", "strength", "habit", "recovery_hrv"]},
                "name":         {"type": "string"},
                "metric":       {"type": "string"},
                "metric_params":{"type": "object"},
                "target_value": {"type": "number"},
                "target_date":  {"type": "string"},
            },
            "required": ["goal_type", "name", "metric", "target_value", "target_date"],
        },
    },
    {
        "name": "add_subgoal",
        "description": "Add a typed subgoal to the user's active primary goal. User MUST confirm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "preset":       {"type": "string", "enum": ["avg_kcal","workouts_per_week","sleep_hours_avg","protein_g_avg","meal_logs_per_week"]},
                "target_value": {"type": "number"},
                "window_days":  {"type": "integer", "minimum": 1, "maximum": 90},
            },
            "required": ["preset", "target_value"],
        },
    },
    {
        "name": "update_goal",
        "description": "Modify the primary goal's target value, target date, or lifecycle status (archive / mark achieved / mark missed). User MUST confirm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_value": {"type": "number"},
                "target_date":  {"type": "string"},
                "status":       {"type": "string", "enum": ["active","achieved","archived","missed"]},
            },
        },
    },
    {
        "name": "get_goal_status",
        "description": "Read-only snapshot of the active primary goal: current value, projection, p_on_pace, milestones, subgoal compliances, today's recommendation. Use this when the user asks about goal progress.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    # Anthropic server-side tool — model invokes it directly
    {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 3,
    },
])

READ_TOOLS.add("get_goal_status")
WRITE_TOOLS.update({"set_primary_goal", "add_subgoal", "update_goal"})
```

**`set_primary_goal` handler responsibilities:**
1. Soft-archive any existing `is_primary=TRUE AND status='active'` row (set `status='archived'`) in the same transaction.
2. Insert the new goal row.
3. Auto-generate milestones: monthly checkpoints from `start_date` to `target_date`, linearly interpolated values. If the total timeline is less than 35 days, generate a single milestone at `target_date` only; if between 35 and 70 days, generate one mid-point milestone plus the final.
4. Compute `start_value` from current data via `compute_current_value()`.
5. Trigger a synchronous initial `daily_goal_recompute` so the goal appears with data immediately.

**`update_goal` validation:**
- `target_date` in the past → reject with `{ok: False, error: "target_date must be in the future"}`.
- `status` transitions are unrestricted — user's call.
- Any field change invalidates today's `goal_recommendations` row (forces a fresh narration on next read).

**`web_search`** is server-side at Anthropic. We don't write a handler. The model invokes it autonomously, capped at 3 uses per turn. System-prompt addendum instructs Claude to use it for best-practice context ("how do people typically lose weight faster?") and cite sources.

### 6. Backend — system prompt extensions

`chat_prompts.build_system_prompt` reads the active primary goal at the top of every chat request (one DB read) and injects one of two blocks:

**When an active goal exists:**

```
Active primary goal: "Lose 15 lbs" — weight from 200 → 185 by 2026-08-01.
Today's recommendation: <narration text>
P_on_pace: 0.34 (off track — uncertain)
Available goal tools: set_primary_goal, add_subgoal, update_goal, get_goal_status.
You also have web_search — use it when the user asks for best-practice context.
```

**When no active goal exists:**

```
The user has no active primary goal. If they ask for help setting one, conduct
a brief structured interview — one question per turn, multiple-choice when sensible:

  Q1. What kind of goal? Options: weight / strength PR / habit consistency / recovery / HRV improvement.
  Q2. Based on Q1, ask for the specific metric (target lbs, exercise + reps, preset + target, target HRV).
  Q3. Target date (default: 12 weeks from today; offer to adjust).
  Q4. Suggest 2–3 typed subgoals appropriate for the goal type. Ask the user to confirm or modify.
  Q5. Summarize the proposed goal + subgoals. Then call set_primary_goal. Subgoals attach via
      separate add_subgoal calls AFTER the primary goal is confirmed.

Push back (not block) on unreasonable timelines: weight loss > 2 lb/wk, target date < 14 days
(non-habit), strength gain > 5% /wk, HRV target > +10 ms over current. State the literature
norm + risk, ask the user to confirm explicitly.

Ask ONE question per turn. Don't call set_primary_goal until Q5 summary is approved.
```

### 7. Frontend — `/goals` page (Layout A)

**New route:** `app/goals/page.tsx` (Next.js server component). Single `fetch(/api/goals/status)`. Renders either `<EmptyGoalState />` or the Layout A composition:

```
<GoalsPage>
  <GoalHeader />                    {/* name · type · target · deadline */}
  <GoalTrajectoryChart />           {/* big chart with CI ribbon */}
  <GoalStatRow />                   {/* 3 cards */}
  <div className="grid-2">
    <MilestoneList />
    <SubgoalList />
  </div>
  <RecommendationCard />            {/* narration + actions + "Ask Claude" link */}
</GoalsPage>
```

**Nav:** `NavHeader.tsx` gains a third link — `Dashboard · Workouts · Goals`.

**`GoalTrajectoryChart`** (the headline element):
- Recharts `ComposedChart`. X axis: dates from `start_date` to `target_date`.
- Solid blue line: observed values (one point per day where data exists).
- Dashed line: posterior mean projection from today forward.
- Shaded area: 95% CI ribbon (recharts `Area` with low/high bounds).
- Horizontal dashed line: `target_value`.
- Vertical accent line: "today" marker.
- Tooltip on hover shows date + value + CI bounds.
- For `habit` goals: `BarChart` of weekly counts vs target, with the same CI envelope shading.
- When `trajectory.method === "insufficient_data"`: renders `"Collecting baseline (N / M data points)"` placeholder.

**`GoalStatRow`** — three equal-width cards:

| card | content | color logic |
|---|---|---|
| Progress | `47%` complete (large) + sub-line `"7.3 of 15 lbs"` | green when `p_on_pace ≥ 0.65`, amber 0.35–0.65, red < 0.35; neutral grey when `p_on_pace == null` |
| Days left | `73` days + sub-line `"deadline Aug 1, 2026"` | red when `< 7 days` and not on pace |
| Projection | `188 lbs (mean)` + sub-line `"34% chance of hitting target"` | same color rule as Progress; greyed out when no projection |

**`MilestoneList`:** vertical list, one row per milestone. Each row shows the target value, target date, and a status badge — `hit on YYYY-MM-DD` (green), `on pace` / `behind` (computed from whether `current >= projected_for_that_date`), or `missed` (red, only when past target_date AND `hit_at == null`).

**`SubgoalList`:** vertical list, one row per subgoal. Each row shows the preset label, target value, and a thin compliance progress bar (0–100%) colored green ≥ 80, amber 60–80, red < 60. Click a row → inline drawer expands beneath it with:
- Sparkline of daily values over `window_days`.
- Numeric daily breakdown (last 7 rows of the relevant data source).
- "Why am I at X%?" plain-language formula (`"avg 2,070 / target 2,100 = 99% of target, but 2 of 7 days were over 2,500"`).
- Only one drawer open at a time; clicking another row collapses the first.

**`RecommendationCard`:** card with today's `narration` in larger type at top, then the `actions` list. Each action: category badge (`nutrition` / `training` / `recovery` / `compliance` / `data`), `change` text bold, `rationale` smaller below. Footer:
- Expandable "Recent recommendations (last 7 days)" — collapses by default; expands to show the trailing week of narrations (one row per day).
- "Ask Claude about this →" link (bottom-right). Click calls `chatDrawerContext.openWith("tell me more about today's recommendation")` — opens the drawer (un-collapses if collapsed) and pre-fills the input. User can edit or hit send.

**`EmptyGoalState`:**

```tsx
<EmptyGoalState>
  <h1>No active goal yet.</h1>
  <p>Tell Claude what you're working toward — they'll walk you through a short interview to set targets, milestones, and supporting subgoals.</p>
  <button onClick={() => openWith("Help me set my first goal")}>
    Help me set my first goal →
  </button>
</EmptyGoalState>
```

### 8. Frontend — chat drawer context

New `lib/chat-drawer-context.tsx`:

```tsx
export const ChatDrawerContext = createContext<{ openWith: (text: string) => void }>({ openWith: () => {} });
```

`app/layout.tsx` wraps children in `<ChatDrawerProvider>` that owns the drawer-open state + a pending-input setter. `ChatDrawer` reads the context's pending input on mount and pre-fills + opens.

### 9. Frontend — type additions (`lib/types.ts`)

```typescript
export type GoalType = "weight" | "strength" | "habit" | "recovery_hrv";
export type SubgoalPreset = "avg_kcal" | "workouts_per_week" | "sleep_hours_avg" | "protein_g_avg" | "meal_logs_per_week";
export type GoalStatusValue = "active" | "achieved" | "archived" | "missed";

export interface Goal {
  id: number; name: string; goal_type: GoalType; metric: string;
  metric_params: Record<string, unknown> | null;
  start_value: number | null; target_value: number;
  start_date: string; target_date: string; status: GoalStatusValue;
}

export interface Trajectory {
  method: string;
  current_value: number | null;
  projected_value_mean: number | null;
  projected_value_ci_low: number | null;
  projected_value_ci_high: number | null;
  p_on_pace: number | null;
  confidence: "high" | "med" | "low";
  data_points_used: number;
  posterior_params?: Record<string, number>;
  min_required?: number;
}

export interface Milestone { target_value: number; target_date: string; hit_at: string | null; hit_value: number | null; }

export interface Subgoal {
  preset: SubgoalPreset; target_value: number; window_days: number;
  current_value: number | null; compliance_pct: number;
}

export interface GoalAction { category: "nutrition" | "training" | "recovery" | "compliance" | "data"; change: string; rationale: string; }
export interface GoalRecommendationView { rec_date: string; narration: string; actions: GoalAction[]; }

export interface GoalStatus {
  goal: Goal | null;
  trajectory: Trajectory | null;
  milestones: Milestone[];
  subgoals: Subgoal[];
  recommendation: GoalRecommendationView | null;
}
```

### 10. Frontend — API helpers (`lib/api.ts`)

```typescript
export async function fetchGoalStatus(userId = "hugo"): Promise<GoalStatus> { ... }
export async function fetchGoalHistory(userId = "hugo", days = 7): Promise<{ rows: GoalRecommendationView[] }> { ... }
```

## Data flow examples

### Brainstorm-style first goal setup

1. User visits `/goals`. No active goal → `<EmptyGoalState />` renders with "Help me set my first goal" button.
2. User clicks button → chat drawer opens, input pre-filled with `"Help me set my first goal"`, sent immediately.
3. Backend: `build_system_prompt` sees no active goal, injects the interview addendum.
4. Claude: "What kind of goal? (1) weight, (2) strength PR, (3) habit consistency, (4) recovery/HRV?"
5. User: "1, weight loss."
6. Claude: "Your current weight is 200 lbs (from your last log). What's your target?"
7. User: "185."
8. Claude: "What's your target date? Default 12 weeks from today (Aug 12, 2026)."
9. User: "Aug 1 instead."
10. Claude pushes back gently: "That's 10 weeks for 15 lbs — 1.5 lb/week is at the upper end of sustainable. Comfortable with that, or shift to 12 weeks?" User confirms 10 weeks.
11. Claude: "I'll suggest these subgoals: (a) avg 2,100 kcal/day (your maintenance minus 400), (b) protein 150g/day, (c) 4 workouts/week. Modify or accept?"
12. User accepts.
13. Claude summarizes + calls `set_primary_goal({goal_type:'weight', name:'Lose 15 lbs', metric:'weight_lbs', target_value:185, target_date:'2026-08-01'})`.
14. Tool-use card renders with the parsed values. User clicks Confirm.
15. Backend: archives prior goal (none in this case), inserts row, computes `start_value=200`, generates milestones (195 by 06-15, 190 by 07-15, 185 by 08-01), runs initial recompute.
16. Claude follows up: three `add_subgoal` calls, each confirmed in turn.
17. User refreshes `/goals` → Layout A renders with chart, milestones, subgoals, and the initial recommendation.

### Daily morning recommendation

1. APScheduler fires `daily_goal_recompute` at 09:00 ET.
2. For each active goal: read last 30/60d of relevant data, run Bayesian model.
3. `p_on_pace = 0.34` → off track. Compute kcal delta needed.
4. Regulation engine says today is OK to deficit (recovery=72, sleep=ok).
5. `compose_actions` produces: `[{nutrition, "-100 kcal", "off pace by 3 lbs"}, {training, "+1 strength session", "workout frequency below target"}, {compliance, "hit avg_kcal target", "current 75%"}]`.
6. `signals_hash` differs from yesterday → call haiku for narration.
7. Narration: "You're 3 lbs ahead of pace but workout frequency is slipping — keep kcal steady and add one mid-week session."
8. Row written to `goal_recommendations`.
9. User opens `/goals` mid-morning. UI fetches `/api/goals/status` → reads today's row.
10. `RecommendationCard` renders the narration + 3 actions.

### On-demand chat with web search

1. User opens chat: "How fast do people typically lose 15 lbs?"
2. Backend system prompt includes the active goal + the web_search note.
3. Claude (sonnet) invokes `web_search` with "sustainable weight loss rate 15 pounds timeline".
4. Anthropic's server returns 3 cited results.
5. Claude synthesizes: "Most evidence suggests 0.5–2 lb/week is sustainable. For 15 lbs that's typically 8–30 weeks. Your goal is 10 weeks (1.5 lb/week), which sits at the upper end — doable but requires consistent adherence. Sources: [link1] [link2] [link3]. Your current trajectory projects 188 lbs by Aug 1 (34% chance of hitting 185)."

### Subgoal compliance drilldown

1. User on `/goals`, clicks the `workouts_per_week` subgoal row (currently 75%).
2. Inline drawer expands below the row.
3. Shows sparkline of weekly counts over last 4 weeks: `[5, 4, 3, 3]`.
4. Daily breakdown: list of the 7 most-recent days, marked workout / rest.
5. Plain-language: "3 workouts in the last 7 days; target is 4. To hit 80%+ compliance you'd need 4 of 5 most-recent weeks at or above target."
6. Clicking another subgoal row collapses this drawer, opens the next.

## Error handling

- **Insufficient data:** `projection.py` returns the `insufficient_data` shape; daily job still writes a row with a single `data`-category action. UI renders "Collecting baseline (N / M)" placeholder.
- **Bayesian fit failures:** Caught in try/except; fall back to `insufficient_data` shape. Logged as `projection_fit_failed`.
- **Daily job failures:** Per-goal isolation. After 3 consecutive failures, `/goals` banners "Recommendation not updated since YYYY-MM-DD."
- **Outlier measurements:** Not auto-detected in v4. User can delete via chat (extension of existing `log_weight` semantics; surface in spec only if needed during build).
- **Unreasonable goal timelines:** System-prompt interview pushes back but allows override. Goal created with `confidence="low"` and a one-time "your timeline is aggressive — consider extending" action.
- **`update_goal` with past `target_date`:** Rejected, error returned.
- **No `start_value` available** (e.g., strength goal with no PR data): `start_value=NULL` allowed. Trajectory shows `insufficient_data` until the first qualifying observation.
- **`web_search` failures:** Anthropic returns an error SSE event; chat drawer's existing error renderer surfaces it. Claude is instructed to recover ("couldn't reach the web — here's what your data shows").
- **Cache invalidation:** `signals_hash` covers `(goal_id, current_value, p_on_pace bucket, subgoal compliances rounded, regulation rec_type)`. `update_goal` always invalidates today's row.
- **Concurrency:** Daily job locks per `(user_id, goal_id, rec_date)`. Chat-side mutations use natural Postgres row-level isolation.

## Testing strategy

**Backend** (`tests/`):

| file | scope |
|---|---|
| `test_projection_weight.py` | synthetic data → expected posterior; insufficient-data fallback; outlier dampening via prior |
| `test_projection_strength.py` | Epley-style 1RM proxy; weekly gain rate posterior |
| `test_projection_habit.py` | Beta-Binomial edges (zero workouts, daily workouts) |
| `test_projection_hrv.py` | slope posterior with smoothing |
| `test_action_composition.py` | table-driven `p_on_pace × regulation × compliance → actions`; deload veto rule |
| `test_milestone_detection.py` | hit detection: insert measurements, assert `hit_at` flips |
| `test_jobs_daily_goal_recompute.py` | mock LLM; assert row written; cache reuse on identical signals_hash |
| `test_routes_goals.py` | GET /api/goals/status (active + empty + insufficient-data); /api/goals/history |
| `test_chat_tools_goals.py` | `set_primary_goal` (archives prior + milestones + initial recompute); `add_subgoal`; `update_goal` (status + date validation); `get_goal_status` |
| `test_chat_route_web_search.py` | tool declaration present; mock SDK web_search event streams to client as text |

**Dashboard** (`tests/`):

| file | scope |
|---|---|
| `goals.test.tsx` | empty state + active state + "Help me set my first goal" calls openWith |
| `goal-trajectory-chart.test.tsx` | CI ribbon when trajectory present; placeholder on insufficient_data |
| `goal-stat-row.test.tsx` | color logic per `p_on_pace` thresholds |
| `milestone-list.test.tsx` | hit/missed/upcoming badges; missed only when past date AND not hit |
| `subgoal-list.test.tsx` | compliance bars; drawer expand on click; auto-collapse |
| `recommendation-card.test.tsx` | renders narration + actions; "Ask Claude" calls openWith |
| `chat-drawer-context.test.tsx` | `openWith` opens drawer if collapsed AND pre-fills input |
| `e2e/v4.spec.ts` | `/goals` empty state on fresh user; drawer pre-fill; (with fixture goal) all panels render |

**Coverage target:** every Bayesian model has happy path + insufficient data + numerical-failure tests. Every chat tool has happy path + one validation-error. Every UI component has render + interaction tests.

## Out-of-plan followups (deferred to v5+)

See "Out" list under Scope above. The notable ones likely to come back:

- **Multiple parallel goals.** Most likely v5 ask once one-goal-at-a-time feels constraining.
- **Adherence tracking** ("did you follow this recommendation?"). Adds `action_followed_at` to a new `goal_action_log` table; unlocks closed-loop analytics.
- **Cross-goal trade-off reasoning.** Composing two Bayesian models against each other (recomp deficit vs. strength gain). Needs its own design effort.
- **Goal-aware narration on the today strip.** Considered surfacing one goal line on `/` but kept v4 clean.
- **Mobile responsive** — explicitly removed; carries indefinitely until a v5+ ask.

## Task list (handoff to writing-plans)

| # | Task | Repo |
|---|---|---|
| T1 | Backend: alembic migration for `goals` + `milestones` + `subgoals` + `goal_recommendations` tables + ORM models | service |
| T2 | Backend: `jobs/projection.py` — 4 Bayesian models (weight, strength, habit, hrv) + insufficient-data fallback + numerical-failure handling + tests | service |
| T3 | Backend: `jobs/daily_goals.py` — per-goal recompute + action composition + signals_hash cache + LLM narration + scheduler registration + tests | service |
| T4 | Backend: `routes/goals.py` — `/api/goals/status` + `/api/goals/history` + tests | service |
| T5 | Backend: extend `chat_tools.py` — `set_primary_goal`, `add_subgoal`, `update_goal`, `get_goal_status` + web_search declaration + tests | service |
| T6 | Backend: extend `chat_prompts.build_system_prompt` — inject active-goal block OR no-goal interview addendum + tests | service |
| T7 | Backend: deploy + run alembic migration on prod | infra |
| T8 | Dashboard: types + API helpers (`Goal`, `Trajectory`, `Milestone`, `Subgoal`, `GoalRecommendationView`, `GoalStatus`, `fetchGoalStatus`, `fetchGoalHistory`) | dashboard |
| T9 | Dashboard: `lib/chat-drawer-context.tsx` + provider in `app/layout.tsx` + ChatDrawer reads pendingInput | dashboard |
| T10 | Dashboard: components — `GoalHeader`, `GoalTrajectoryChart`, `GoalStatRow`, `MilestoneList`, `SubgoalList`, `RecommendationCard`, `EmptyGoalState` + tests | dashboard |
| T11 | Dashboard: `app/goals/page.tsx` + `NavHeader` link + page test | dashboard |
| T12 | Dashboard: e2e Playwright spec (`tests/e2e/v4.spec.ts`) | dashboard |
| T13 | Backend + dashboard: end-to-end smoke — brainstorm interview → goal created → trajectory renders → recommendation surfaces → subgoal drawer + Ask-Claude link | both |
| T14 | Tag releases: backend `v0.6.0-goals`, dashboard `v0.4.0-goals` | both |

**Estimated wall time:** ~4–6 focused days (~14 tasks).

## Decisions captured during brainstorming (2026-05-20)

1. **Four goal types in scope** — weight, strength PR, habit consistency, recovery/HRV. All-of-the-above per Hugo.
2. **One primary goal at a time + subgoals + auto-milestones** — not multi-goal. Auto-archive prior primary when a new one is set.
3. **Daily background recompute + on-demand chat** — extends regulation engine; cached by `signals_hash`.
4. **Web search via chat tool only** — Anthropic's `web_search_20250305`, max 3 uses per turn. Daily background uses local data only.
5. **Bayesian projection** — closed-form conjugate updates; scipy not pymc. Normal-Normal for continuous, Beta-Binomial for habit.
6. **Layout A — Hero Focus** for `/goals`. Big trajectory chart + 3-stat row + milestones/subgoals side-by-side + recommendation card.
7. **Chat-driven goal creation** with a confirmation card — same pattern as v3 `log_meal`.
8. **Typed-preset subgoals** with live compliance from existing tables (no separate subgoal_hits log).
9. **Brainstorm-style first goal setup** — system prompt addendum directs Claude to conduct a 5-question structured interview when no active goal exists. No starter-goal templates.
10. **"Ask Claude about this" link on RecommendationCard** — pre-fills the chat drawer with `"tell me more about today's recommendation"`.
11. **Inline subgoal compliance drawer** — sparkline + numeric daily breakdown + plain-language formula. One drawer open at a time.
12. **Push back, not block, on aggressive timelines** — Claude states risk + literature norm; user can override with explicit confirmation. Goal flagged `confidence="low"`.
