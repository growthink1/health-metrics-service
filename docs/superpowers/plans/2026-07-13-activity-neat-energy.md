# Item #6 — Structured Activity Logging + Daily Energy (NEAT/TDEE) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give walks/rides a structured home (`activity_log`), give DEXA a home (`body_composition`), and surface a same-day energy readout on the session brief that blends a calibrated modeled TDEE (RMR × baseline + NEAT) with Whoop's measured expenditure.

**Architecture:** New tables + insert-only routes + MCP tools mirror the existing `meals_v1` / `log_meal` pattern. The energy math is a pure module (`energy.py`) fed by a brief-layer DB glue function (`compute_energy_today`), exactly like `water_retention.py` + `compute_weight_trend`. The engine (`engine.py`) stays I/O-free and untouched. RMR is computed via Katch-McArdle from the latest `body_composition` row, with a config fallback. Constants are tuned by an offline script.

**Tech Stack:** FastAPI, async SQLAlchemy 2.0, Alembic, Pydantic v2, Postgres. Python 3.12 on CI (3.11 local venv).

**Spec:** `docs/superpowers/specs/2026-07-13-activity-neat-energy-design.md`

## Global Constraints

- **Conventional commits, NO co-author trailer** (repo convention).
- **`engine.py` stays I/O-free** (Invariant #2) — all new computation lives in the brief layer / pure modules.
- **`date.today()`/`datetime.now()` only in the brief glue, never in pure modules** — pure functions take dates/flags as params so tests are deterministic.
- **New route + test files must be added to BOTH ruff lists in `.github/workflows/ci.yml`** (Task 11) or CI's ruff step fails.
- **The regulation 100% coverage gate is scoped to `tests/regulation/test_engine.py`** — do not add DB-touching tests to a coverage-gated path; DB tests segfault under coverage (asyncpg+greenlet).
- **Local venv:** `source .venv/bin/activate` before pytest/ruff. macOS DB tests may segfault under `--cov`; run them without coverage.
- **Every new symbol referenced across tasks uses the exact signatures in the Interfaces blocks.**

---

### Task 1: ORM models — `ActivityLog` + `BodyComposition`

**Files:**
- Modify: `src/health_metrics/models.py` (append two classes)
- Test: `tests/test_models_migration.py` (extend the canary set)

**Interfaces:**
- Produces: `ActivityLog` (table `activity_log`) and `BodyComposition` (table `body_composition`) ORM classes.

- [ ] **Step 1: Update the canary test to expect the two new tables**

In `tests/test_models_migration.py`, `test_all_expected_tables_registered` asserts `tables == {…}`. Add `"activity_log"` and `"body_composition"` to that set. Then add two constraint tests at the end of the file:

```python
def test_activity_log_check_constraints():
    t = Base.metadata.tables["activity_log"]
    checks = {c.name for c in t.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert "activity_log_type_check" in checks
    assert "activity_log_source_check" in checks


def test_body_composition_check_constraint():
    t = Base.metadata.tables["body_composition"]
    checks = {c.name for c in t.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert "body_composition_source_check" in checks
```

- [ ] **Step 2: Run the canary test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_models_migration.py::test_all_expected_tables_registered -q`
Expected: FAIL (set mismatch — new tables not registered).

- [ ] **Step 3: Append the two ORM models to `models.py`**

At the end of `src/health_metrics/models.py`:

```python
class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    activity_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    activity_type: Mapped[str] = mapped_column(Text, nullable=False)
    distance_mi: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    duration_min: Mapped[Optional[int]] = mapped_column(Integer)
    elevation_ft: Mapped[Optional[int]] = mapped_column(Integer)
    avg_hr: Mapped[Optional[int]] = mapped_column(Integer)
    max_hr: Mapped[Optional[int]] = mapped_column(Integer)
    strain: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        CheckConstraint(
            "activity_type IN ('walk','run','ride','z2','hiit','strength','climb','other')",
            name="activity_log_type_check",
        ),
        CheckConstraint(
            "source IN ('strava','whoop','peloton','manual','api')",
            name="activity_log_source_check",
        ),
        Index("idx_activity_log_user_date", "user_id", "activity_date"),
    )


class BodyComposition(Base):
    __tablename__ = "body_composition"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    measured_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    weight_lbs: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    body_fat_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 1))
    lean_mass_lbs: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    fat_mass_lbs: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        CheckConstraint(
            "source IN ('dexa','bioimpedance','hydrostatic','manual')",
            name="body_composition_source_check",
        ),
        Index("idx_body_comp_user_date", "user_id", "measured_date"),
    )
```

- [ ] **Step 4: Run the canary tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_models_migration.py -q`
Expected: PASS (all, incl. the two new constraint tests).

- [ ] **Step 5: Commit**

```bash
git add src/health_metrics/models.py tests/test_models_migration.py
git commit -m "feat(models): activity_log + body_composition ORM models"
```

---

### Task 2: Alembic migration for both tables

**Files:**
- Create: `alembic/versions/<rev>_activity_and_body_composition.py`
- Test: manual round-trip against local Postgres.

**Interfaces:**
- Consumes: Task 1 models. Produces: prod-ready DDL; `alembic heads` advances by one.

- [ ] **Step 1: Generate a blank revision**

Run: `source .venv/bin/activate && alembic revision -m "activity_log and body_composition tables"`
Note the created filename. Its `down_revision` must be `9abd8e07b339` (current head — confirm with `alembic heads`).

- [ ] **Step 2: Fill in the migration body**

```python
def upgrade() -> None:
    op.create_table(
        "activity_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("activity_type", sa.Text(), nullable=False),
        sa.Column("distance_mi", sa.Numeric(6, 2), nullable=True),
        sa.Column("duration_min", sa.Integer(), nullable=True),
        sa.Column("elevation_ft", sa.Integer(), nullable=True),
        sa.Column("avg_hr", sa.Integer(), nullable=True),
        sa.Column("max_hr", sa.Integer(), nullable=True),
        sa.Column("strain", sa.Numeric(4, 2), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "activity_type IN ('walk','run','ride','z2','hiit','strength','climb','other')",
            name="activity_log_type_check",
        ),
        sa.CheckConstraint(
            "source IN ('strava','whoop','peloton','manual','api')",
            name="activity_log_source_check",
        ),
    )
    op.create_index("idx_activity_log_user_date", "activity_log", ["user_id", "activity_date"])

    op.create_table(
        "body_composition",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("measured_date", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("weight_lbs", sa.Numeric(5, 2), nullable=True),
        sa.Column("body_fat_pct", sa.Numeric(4, 1), nullable=True),
        sa.Column("lean_mass_lbs", sa.Numeric(5, 2), nullable=True),
        sa.Column("fat_mass_lbs", sa.Numeric(5, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "source IN ('dexa','bioimpedance','hydrostatic','manual')",
            name="body_composition_source_check",
        ),
    )
    op.create_index("idx_body_comp_user_date", "body_composition", ["user_id", "measured_date"])


def downgrade() -> None:
    op.drop_index("idx_body_comp_user_date", table_name="body_composition")
    op.drop_table("body_composition")
    op.drop_index("idx_activity_log_user_date", table_name="activity_log")
    op.drop_table("activity_log")
```

- [ ] **Step 3: Round-trip against local Postgres**

Run (local DB must be up on :5433):
```bash
source .venv/bin/activate
export DATABASE_URL="postgresql+asyncpg://hms:hms_dev_password@localhost:5433/health_metrics"
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
```
Expected: no errors; final `alembic current` shows the new revision as head.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/
git commit -m "feat(db): migration for activity_log + body_composition"
```

---

### Task 3: `body_composition.py` — Katch-McArdle RMR (pure)

**Files:**
- Create: `src/health_metrics/regulation/body_composition.py`
- Test: `tests/regulation/test_body_composition.py`

**Interfaces:**
- Produces: `katch_mcardle_rmr(lean_mass_lbs: float) -> int` — RMR kcal, rounded to int. `LBS_PER_KG = 2.20462`.

- [ ] **Step 1: Write the failing test**

```python
from health_metrics.regulation.body_composition import katch_mcardle_rmr


def test_katch_mcardle_known_value():
    # 170 lb lean mass = 77.11 kg → 370 + 21.6*77.11 = 2035.7 → 2036
    assert katch_mcardle_rmr(170.0) == 2036


def test_katch_mcardle_monotonic():
    assert katch_mcardle_rmr(180.0) > katch_mcardle_rmr(150.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `source .venv/bin/activate && pytest tests/regulation/test_body_composition.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
"""Katch-McArdle RMR from lean body mass. Pure — no DB, no network.

RMR_kcal = 370 + 21.6 * lean_mass_kg
"""

from __future__ import annotations

LBS_PER_KG = 2.20462


def katch_mcardle_rmr(lean_mass_lbs: float) -> int:
    lean_mass_kg = lean_mass_lbs / LBS_PER_KG
    return round(370 + 21.6 * lean_mass_kg)
```

- [ ] **Step 4: Run to verify it passes**

Run: `source .venv/bin/activate && pytest tests/regulation/test_body_composition.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/health_metrics/regulation/body_composition.py tests/regulation/test_body_composition.py
git commit -m "feat(regulation): Katch-McArdle RMR from lean mass"
```

---

### Task 4: `energy_config.py` — per-user constants + type maps

**Files:**
- Create: `src/health_metrics/regulation/energy_config.py`
- Test: `tests/regulation/test_energy_config.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) EnergyParams(baseline_activity_factor: float, neat_coef: float, fallback_rmr_kcal: int, divergence_pct: float)`
  - `get_energy_params(user_id: str) -> EnergyParams`
  - `NORMALIZE_TYPE: dict[str, str]` — maps `workouts.workout_type` → `activity_type` enum value.
  - `PER_TYPE_KCAL_PER_MIN: dict[str, float]` — duration-only fallback, keyed by normalized `activity_type`.

- [ ] **Step 1: Write the failing test**

```python
from health_metrics.regulation.energy_config import (
    NORMALIZE_TYPE,
    PER_TYPE_KCAL_PER_MIN,
    get_energy_params,
)


def test_default_params_present():
    p = get_energy_params("hugo")
    assert 1.2 <= p.baseline_activity_factor <= 1.5
    assert p.neat_coef > 0
    assert p.fallback_rmr_kcal > 0
    assert 0 < p.divergence_pct < 1


def test_unknown_user_falls_back():
    assert get_energy_params("nobody") == get_energy_params("hugo")


def test_type_normalization_covers_whoop_types():
    assert NORMALIZE_TYPE["walking"] == "walk"
    assert NORMALIZE_TYPE["cycling"] == "ride"
    assert NORMALIZE_TYPE["functional-fitness"] == "strength"


def test_per_type_kcal_has_default_key():
    assert "other" in PER_TYPE_KCAL_PER_MIN
```

- [ ] **Step 2: Run to verify it fails**

Run: `source .venv/bin/activate && pytest tests/regulation/test_energy_config.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
"""Per-user energy-model constants + activity-type maps.

Constants are SEED values pending offline calibration
(scripts/calibrate_energy.py) against whoop_kcal_burned + Kalman revealed_tdee.
Re-run and update after body composition shifts. See
docs/superpowers/specs/2026-07-13-activity-neat-energy-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnergyParams:
    baseline_activity_factor: float  # sedentary desk multiplier on RMR (~3k steps)
    neat_coef: float  # net-of-resting kcal per mile per lb, for distance walks/runs
    fallback_rmr_kcal: int  # used only when no body_composition row exists
    divergence_pct: float  # |measured-modeled|/modeled threshold for the flag


# SEED constants (pre-calibration). baseline 1.35 = sedentary; walks add on top.
_DEFAULT = EnergyParams(baseline_activity_factor=1.35, neat_coef=0.53, fallback_rmr_kcal=2000, divergence_pct=0.10)

_PARAMS_BY_USER: dict[str, EnergyParams] = {
    "hugo": _DEFAULT,
    "andrea": _DEFAULT,
}

# workouts.workout_type -> activity_type enum value
NORMALIZE_TYPE: dict[str, str] = {
    "walking": "walk",
    "walk": "walk",
    "running": "run",
    "run": "run",
    "cycling": "ride",
    "ride": "ride",
    "functional-fitness": "strength",
    "weightlifting": "strength",
    "strength": "strength",
    "climbing": "climb",
    "climb": "climb",
    "hiit": "hiit",
    "z2": "z2",
}

# Duration-only fallback kcal/min above resting, by normalized activity_type.
PER_TYPE_KCAL_PER_MIN: dict[str, float] = {
    "walk": 3.0,
    "run": 8.0,
    "ride": 6.0,
    "z2": 6.0,
    "hiit": 9.0,
    "strength": 4.0,
    "climb": 7.0,
    "other": 4.0,
}


def get_energy_params(user_id: str) -> EnergyParams:
    return _PARAMS_BY_USER.get(user_id, _DEFAULT)


def normalize_activity_type(raw: str | None) -> str:
    if raw is None:
        return "other"
    return NORMALIZE_TYPE.get(raw, raw if raw in PER_TYPE_KCAL_PER_MIN else "other")
```

- [ ] **Step 4: Run to verify it passes**

Run: `source .venv/bin/activate && pytest tests/regulation/test_energy_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/health_metrics/regulation/energy_config.py tests/regulation/test_energy_config.py
git commit -m "feat(regulation): energy-model config + activity-type maps"
```

---

### Task 5: `EnergyToday` schema + `SessionBrief.energy_today`

**Files:**
- Modify: `src/health_metrics/regulation/schemas.py`
- Test: `tests/regulation/test_engine.py` is coverage-gated — put this test in `tests/regulation/test_energy.py` (created in Task 6). For now add a minimal shape test in `tests/test_schemas_energy.py`.

**Interfaces:**
- Produces: `EnergyToday` Pydantic model + `SessionBrief.energy_today: EnergyToday | None`.

- [ ] **Step 1: Write the failing test** (`tests/test_schemas_energy.py`)

```python
from health_metrics.regulation.schemas import EnergyToday, SessionBrief


def test_energy_today_shape():
    e = EnergyToday(
        neat_kcal=120.0,
        baseline_kcal=2700,
        rmr_kcal=2000,
        tdee_measured_kcal=2650,
        tdee_modeled_kcal=2820,
        tdee_estimate_kcal=2820,
        divergence_flag=False,
        activities_counted=["walk 2.7mi (activity_log)"],
        rmr_source="dexa",
    )
    assert e.tdee_estimate_kcal == 2820


def test_session_brief_energy_today_optional():
    assert "energy_today" in SessionBrief.model_fields
```

- [ ] **Step 2: Run to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_schemas_energy.py -q`
Expected: FAIL (ImportError / field missing).

- [ ] **Step 3: Implement — add to `schemas.py`**

Add the model (place it just before `class SessionBrief`):

```python
class EnergyToday(BaseModel):
    """Same-day energy readout: measured (Whoop) + modeled (RMR+NEAT) blend."""

    neat_kcal: float | None = None
    baseline_kcal: int | None = None  # rmr_kcal * baseline_activity_factor
    rmr_kcal: int | None = None
    tdee_measured_kcal: int | None = None  # whoop_kcal_burned, complete days only
    tdee_modeled_kcal: int | None = None  # baseline + neat
    tdee_estimate_kcal: int | None = None  # headline = modeled
    divergence_flag: bool = False  # measured vs modeled disagree > divergence_pct
    activities_counted: list[str] = Field(default_factory=list)
    rmr_source: Literal["dexa", "fallback"] = "fallback"
```

Add the field to `SessionBrief` (after `weight_trend`):

```python
    energy_today: EnergyToday | None = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_schemas_energy.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/health_metrics/regulation/schemas.py tests/test_schemas_energy.py
git commit -m "feat(schemas): EnergyToday + SessionBrief.energy_today"
```

---

### Task 6: `energy.py` — NEAT dedup + blend (pure)

**Files:**
- Create: `src/health_metrics/regulation/energy.py`
- Test: `tests/regulation/test_energy.py`

**Interfaces:**
- Consumes: `EnergyParams` (Task 4), `EnergyToday` (Task 5).
- Produces:
  - `@dataclass(frozen=True) Activity(activity_type: str, source_layer: str, distance_mi: float | None, duration_min: int | None, kcal: float | None)` — `source_layer` ∈ `"manual"|"auto"`; `activity_type` already normalized.
  - `dedup_activities(activities: list[Activity]) -> list[Activity]` — drop `auto` entries whose type also appears in a `manual` entry.
  - `activity_neat_kcal(a: Activity, weight_lbs: float | None, params: EnergyParams) -> float`
  - `neat_kcal(activities: list[Activity], weight_lbs: float | None, params: EnergyParams) -> float`
  - `activity_label(a: Activity) -> str`
  - `compute_energy(rmr_kcal: int, rmr_source: str, weight_lbs: float | None, activities: list[Activity], whoop_kcal_burned: int | None, whoop_complete: bool, params: EnergyParams) -> EnergyToday`

- [ ] **Step 1: Write the failing tests**

```python
from health_metrics.regulation.energy import (
    Activity,
    compute_energy,
    dedup_activities,
    neat_kcal,
)
from health_metrics.regulation.energy_config import get_energy_params

P = get_energy_params("hugo")


def test_dedup_manual_wins_over_auto_same_type():
    acts = [
        Activity("walk", "auto", None, 60, 90.0),
        Activity("walk", "manual", 2.7, 55, None),
    ]
    out = dedup_activities(acts)
    assert len(out) == 1
    assert out[0].source_layer == "manual"


def test_dedup_keeps_distinct_types():
    acts = [
        Activity("walk", "auto", None, 60, 90.0),
        Activity("ride", "auto", None, 41, 442.0),
    ]
    assert len(dedup_activities(acts)) == 2


def test_neat_distance_formula():
    # 2.7 mi * 220 lb * 0.53 = 314.8 ... seed coef; assert the arithmetic path
    a = Activity("walk", "manual", 2.7, 55, None)
    val = neat_kcal([a], 220.0, P)
    assert abs(val - (2.7 * 220.0 * P.neat_coef)) < 0.01


def test_neat_prefers_measured_kcal():
    a = Activity("ride", "auto", None, 41, 442.0)
    assert neat_kcal([a], 220.0, P) == 442.0


def test_neat_duration_fallback_when_no_distance_or_kcal():
    a = Activity("strength", "manual", None, 45, None)
    from health_metrics.regulation.energy_config import PER_TYPE_KCAL_PER_MIN
    assert neat_kcal([a], 220.0, P) == 45 * PER_TYPE_KCAL_PER_MIN["strength"]


def test_compute_energy_headline_is_modeled():
    acts = [Activity("walk", "manual", 2.7, 55, None)]
    e = compute_energy(2000, "dexa", 220.0, acts, whoop_kcal_burned=2650, whoop_complete=True, params=P)
    assert e.baseline_kcal == round(2000 * P.baseline_activity_factor)
    assert e.tdee_modeled_kcal == e.baseline_kcal + round(e.neat_kcal)
    assert e.tdee_estimate_kcal == e.tdee_modeled_kcal  # headline = modeled
    assert e.tdee_measured_kcal == 2650


def test_compute_energy_partial_day_excludes_measured():
    e = compute_energy(2000, "dexa", 220.0, [], whoop_kcal_burned=807, whoop_complete=False, params=P)
    assert e.tdee_measured_kcal is None
    assert e.divergence_flag is False


def test_compute_energy_divergence_flag():
    # modeled ~2700, measured 2000 → >10% divergence
    e = compute_energy(2000, "dexa", 220.0, [], whoop_kcal_burned=2000, whoop_complete=True, params=P)
    assert e.tdee_modeled_kcal == round(2000 * P.baseline_activity_factor)
    assert e.divergence_flag is True


def test_active_day_beats_sedentary_day():
    sed = compute_energy(2000, "dexa", 220.0, [], None, False, P)
    act = compute_energy(2000, "dexa", 220.0, [Activity("walk", "manual", 2.7, 55, None)], None, False, P)
    assert act.tdee_estimate_kcal > sed.tdee_estimate_kcal
```

- [ ] **Step 2: Run to verify it fails**

Run: `source .venv/bin/activate && pytest tests/regulation/test_energy.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `energy.py`**

```python
"""Daily energy model: dedup activity, NEAT term, measured/modeled blend.

Pure — no DB, no network, no clock. The brief layer (compute_energy_today in
brief.py) supplies RMR, activities, and the whoop_complete flag. See
docs/superpowers/specs/2026-07-13-activity-neat-energy-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from .energy_config import PER_TYPE_KCAL_PER_MIN, EnergyParams
from .schemas import EnergyToday


@dataclass(frozen=True)
class Activity:
    activity_type: str  # normalized enum value
    source_layer: str  # "manual" | "auto"
    distance_mi: float | None
    duration_min: int | None
    kcal: float | None


def dedup_activities(activities: list[Activity]) -> list[Activity]:
    """Drop auto entries whose type also appears in a manual entry (manual wins)."""
    manual_types = {a.activity_type for a in activities if a.source_layer == "manual"}
    return [a for a in activities if not (a.source_layer == "auto" and a.activity_type in manual_types)]


def activity_neat_kcal(a: Activity, weight_lbs: float | None, params: EnergyParams) -> float:
    if a.kcal is not None:
        return a.kcal
    if a.distance_mi is not None and weight_lbs is not None and a.activity_type in ("walk", "run"):
        return a.distance_mi * weight_lbs * params.neat_coef
    if a.duration_min is not None:
        return a.duration_min * PER_TYPE_KCAL_PER_MIN.get(a.activity_type, PER_TYPE_KCAL_PER_MIN["other"])
    return 0.0


def neat_kcal(activities: list[Activity], weight_lbs: float | None, params: EnergyParams) -> float:
    return sum(activity_neat_kcal(a, weight_lbs, params) for a in dedup_activities(activities))


def activity_label(a: Activity) -> str:
    layer = "activity_log" if a.source_layer == "manual" else "workouts"
    if a.distance_mi is not None:
        return f"{a.activity_type} {a.distance_mi}mi ({layer})"
    if a.duration_min is not None:
        return f"{a.activity_type} {a.duration_min}min ({layer})"
    return f"{a.activity_type} ({layer})"


def compute_energy(
    rmr_kcal: int,
    rmr_source: str,
    weight_lbs: float | None,
    activities: list[Activity],
    whoop_kcal_burned: int | None,
    whoop_complete: bool,
    params: EnergyParams,
) -> EnergyToday:
    deduped = dedup_activities(activities)
    neat = round(sum(activity_neat_kcal(a, weight_lbs, params) for a in deduped), 1)
    baseline = round(rmr_kcal * params.baseline_activity_factor)
    modeled = baseline + round(neat)
    measured = whoop_kcal_burned if whoop_complete else None

    divergence = False
    if measured is not None and modeled > 0:
        divergence = abs(measured - modeled) / modeled > params.divergence_pct

    return EnergyToday(
        neat_kcal=neat,
        baseline_kcal=baseline,
        rmr_kcal=rmr_kcal,
        tdee_measured_kcal=measured,
        tdee_modeled_kcal=modeled,
        tdee_estimate_kcal=modeled,  # headline = modeled (calibrated)
        divergence_flag=divergence,
        activities_counted=[activity_label(a) for a in deduped],
        rmr_source="dexa" if rmr_source == "dexa" else "fallback",
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `source .venv/bin/activate && pytest tests/regulation/test_energy.py -q`
Expected: PASS (all 9).

- [ ] **Step 5: Commit**

```bash
git add src/health_metrics/regulation/energy.py tests/regulation/test_energy.py
git commit -m "feat(regulation): energy model — NEAT dedup + measured/modeled blend"
```

---

### Task 7: brief-layer glue — `compute_energy_today` + wire into brief

**Files:**
- Modify: `src/health_metrics/regulation/brief.py`
- Test: `tests/regulation/test_brief_energy.py`

**Interfaces:**
- Consumes: `energy.compute_energy`, `Activity`; `energy_config.get_energy_params`, `normalize_activity_type`; `body_composition.katch_mcardle_rmr`; models `ActivityLog`, `BodyComposition`, `Workout`, `DailyMetrics`.
- Produces: `async def compute_energy_today(session, user_id, as_of, weight_lbs, today) -> EnergyToday | None`. `today` param (a `date`) is passed so the function is deterministic in tests; `compute_session_brief` passes `date.today()`.

- [ ] **Step 1: Write the failing test**

```python
from datetime import date

import pytest

from health_metrics.models import ActivityLog, BodyComposition, DailyMetrics
from health_metrics.regulation.brief import compute_energy_today


@pytest.mark.asyncio
async def test_energy_today_uses_dexa_rmr_and_activity(db_session, test_user_id):
    db_session.add(BodyComposition(user_id=test_user_id, measured_date=date(2026, 7, 1), source="dexa", lean_mass_lbs=170.0))
    db_session.add(ActivityLog(user_id=test_user_id, activity_date=date(2026, 7, 13), activity_type="walk", distance_mi=2.7, duration_min=55, source="strava"))
    db_session.add(DailyMetrics(user_id=test_user_id, metric_date=date(2026, 7, 13), whoop_kcal_burned=2650))
    await db_session.flush()

    e = await compute_energy_today(db_session, test_user_id, date(2026, 7, 13), weight_lbs=220.0, today=date(2026, 7, 14))
    assert e is not None
    assert e.rmr_source == "dexa"
    assert e.rmr_kcal == 2036
    assert 110 <= e.neat_kcal <= 125  # 2.7mi walk, seed coef
    assert e.tdee_measured_kcal == 2650  # complete day (as_of < today)


@pytest.mark.asyncio
async def test_energy_today_partial_today_excludes_measured(db_session, test_user_id):
    db_session.add(DailyMetrics(user_id=test_user_id, metric_date=date(2026, 7, 13), whoop_kcal_burned=807))
    await db_session.flush()
    e = await compute_energy_today(db_session, test_user_id, date(2026, 7, 13), weight_lbs=220.0, today=date(2026, 7, 13))
    assert e.tdee_measured_kcal is None  # as_of == today → partial
    assert e.rmr_source == "fallback"  # no body_composition row


@pytest.mark.asyncio
async def test_energy_today_dedups_whoop_and_manual_walk(db_session, test_user_id):
    from health_metrics.models import Workout
    from datetime import datetime, UTC
    db_session.add(Workout(user_id=test_user_id, workout_date=date(2026, 7, 13), source="whoop", source_id="w1", workout_type="walking", started_at=datetime(2026, 7, 13, tzinfo=UTC), duration_min=60, kcal=90))
    db_session.add(ActivityLog(user_id=test_user_id, activity_date=date(2026, 7, 13), activity_type="walk", distance_mi=2.7, duration_min=55, source="strava"))
    await db_session.flush()
    e = await compute_energy_today(db_session, test_user_id, date(2026, 7, 13), weight_lbs=220.0, today=date(2026, 7, 14))
    # Only ONE walk counted (manual wins) — not 90 + 315
    assert len(e.activities_counted) == 1
    assert "activity_log" in e.activities_counted[0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `source .venv/bin/activate && pytest tests/regulation/test_brief_energy.py -q`
Expected: FAIL (`compute_energy_today` not defined).

- [ ] **Step 3: Implement — add to `brief.py`**

Add imports at the top of `brief.py` (near the other regulation imports):

```python
from .body_composition import katch_mcardle_rmr
from .energy import Activity, compute_energy
from .energy_config import get_energy_params, normalize_activity_type
```

Add the ORM imports to the existing `from ..models import ...` line: `ActivityLog`, `BodyComposition` (Workout + DailyMetrics + ManualLog already imported).

Add the function (after `compute_weight_trend`, before `compute_session_brief`):

```python
async def _fetch_day_activities(session: AsyncSession, user_id: str, as_of: date_type) -> list[Activity]:
    """Union of auto workouts + manual activity_log for the day, normalized."""
    acts: list[Activity] = []

    wr = await session.execute(
        select(Workout.workout_type, Workout.duration_min, Workout.kcal).where(
            Workout.user_id == user_id,
            Workout.workout_date == as_of,
        )
    )
    for wtype, dur, kcal in wr.all():
        acts.append(
            Activity(
                activity_type=normalize_activity_type(wtype),
                source_layer="auto",
                distance_mi=None,
                duration_min=dur,
                kcal=float(kcal) if kcal is not None else None,
            )
        )

    ar = await session.execute(
        select(
            ActivityLog.activity_type, ActivityLog.distance_mi, ActivityLog.duration_min
        ).where(
            ActivityLog.user_id == user_id,
            ActivityLog.activity_date == as_of,
        )
    )
    for atype, dist, dur in ar.all():
        acts.append(
            Activity(
                activity_type=normalize_activity_type(atype),
                source_layer="manual",
                distance_mi=float(dist) if dist is not None else None,
                duration_min=dur,
                kcal=None,
            )
        )
    return acts


async def compute_energy_today(
    session: AsyncSession,
    user_id: str,
    as_of: date_type,
    weight_lbs: float | None,
    today: date_type,
) -> EnergyToday | None:
    params = get_energy_params(user_id)

    # RMR from the latest body_composition row with lean mass; else fallback.
    br = await session.execute(
        select(BodyComposition.lean_mass_lbs)
        .where(
            BodyComposition.user_id == user_id,
            BodyComposition.lean_mass_lbs.is_not(None),
            BodyComposition.measured_date <= as_of,
        )
        .order_by(BodyComposition.measured_date.desc())
        .limit(1)
    )
    lean = br.scalar_one_or_none()
    if lean is not None:
        rmr_kcal, rmr_source = katch_mcardle_rmr(float(lean)), "dexa"
    else:
        rmr_kcal, rmr_source = params.fallback_rmr_kcal, "fallback"

    activities = await _fetch_day_activities(session, user_id, as_of)

    dr = await session.execute(
        select(DailyMetrics.whoop_kcal_burned).where(
            DailyMetrics.user_id == user_id,
            DailyMetrics.metric_date == as_of,
        )
    )
    whoop_kcal = dr.scalar_one_or_none()
    whoop_complete = as_of < today  # today's whoop total is partial

    return compute_energy(
        rmr_kcal=rmr_kcal,
        rmr_source=rmr_source,
        weight_lbs=weight_lbs,
        activities=activities,
        whoop_kcal_burned=whoop_kcal,
        whoop_complete=whoop_complete,
        params=params,
    )
```

Add `EnergyToday` to the `from .schemas import (...)` block.

- [ ] **Step 4: Wire into `compute_session_brief`**

After `weight_trend = await compute_weight_trend(session, user_id, as_of)` add:

```python
    _wt_weight = None
    if weight_trend is not None:
        _wt_weight = weight_trend.filtered_weight_lbs or weight_trend.current_lbs
    energy_today = await compute_energy_today(
        session, user_id, as_of, weight_lbs=_wt_weight, today=date_type.today()
    )
```

And add `energy_today=energy_today,` to the `SessionBrief(...)` return.

- [ ] **Step 5: Run to verify it passes**

Run: `source .venv/bin/activate && pytest tests/regulation/test_brief_energy.py -q`
Expected: PASS (3 tests). (If macOS segfaults under coverage, run without `--cov` — this path is not coverage-gated.)

- [ ] **Step 6: Commit**

```bash
git add src/health_metrics/regulation/brief.py tests/regulation/test_brief_energy.py
git commit -m "feat(regulation): compute_energy_today wired into session brief"
```

---

### Task 8: `POST /api/v1/activities` route + `log_activity` path

**Files:**
- Create: `src/health_metrics/routes/activities.py`
- Modify: `src/health_metrics/main.py` (register router)
- Test: `tests/test_routes_activities.py`

**Interfaces:**
- Consumes: `ActivityLog` model, `invalidate_cache`.
- Produces: `POST /api/v1/activities` → 201 with created row incl. `id`.

- [ ] **Step 1: Write the failing tests**

```python
from contextlib import asynccontextmanager
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import ActivityLog


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_activity_401_without_token(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import activities as act_route
    monkeypatch.setattr(act_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/activities", json={"activity_type": "walk", "activity_date": "2026-07-13", "source": "strava"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_activity_insert_roundtrip(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import activities as act_route
    monkeypatch.setattr(act_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/activities",
            headers={"Authorization": "Bearer dash-tok"},
            json={"user_id": test_user_id, "activity_date": "2026-07-13", "activity_type": "walk", "distance_mi": 2.7, "duration_min": 55, "source": "strava"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] > 0
    assert body["activity_type"] == "walk"
    r = await db_session.execute(select(ActivityLog).where(ActivityLog.user_id == test_user_id, ActivityLog.activity_date == date(2026, 7, 13)))
    assert r.scalar_one().activity_type == "walk"


@pytest.mark.asyncio
async def test_two_activities_same_day_both_persist(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import activities as act_route
    monkeypatch.setattr(act_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        h = {"Authorization": "Bearer dash-tok"}
        await client.post("/api/v1/activities", headers=h, json={"user_id": test_user_id, "activity_date": "2026-07-13", "activity_type": "walk", "source": "strava"})
        await client.post("/api/v1/activities", headers=h, json={"user_id": test_user_id, "activity_date": "2026-07-13", "activity_type": "ride", "source": "peloton"})
    r = await db_session.execute(select(ActivityLog).where(ActivityLog.user_id == test_user_id))
    assert len(r.scalars().all()) == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_routes_activities.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `activities.py`** (mirror `meals_v1.py` POST exactly)

```python
"""POST /api/v1/activities — log a walk/ride/etc. Insert-only; multiple per day."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..models import ActivityLog
from ..regulation.cache import invalidate_cache
from .auth import Principal, get_principal

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1")


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            yield session

    return _ctx()


class ActivityPayload(BaseModel):
    user_id: str = "hugo"
    activity_date: date_type = Field(default_factory=date_type.today)
    activity_type: str
    distance_mi: float | None = None
    duration_min: int | None = None
    elevation_ft: int | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    strain: float | None = None
    source: str = "manual"
    notes: str | None = None


class ActivityResponse(BaseModel):
    id: int
    user_id: str
    activity_date: date_type
    activity_type: str
    distance_mi: float | None
    duration_min: int | None
    elevation_ft: int | None
    avg_hr: int | None
    max_hr: int | None
    strain: float | None
    source: str
    notes: str | None


@router.post("/activities", response_model=ActivityResponse, status_code=201)
async def post_activity(
    payload: ActivityPayload,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> ActivityResponse:
    log.info("activity_write", user_id=payload.user_id, activity_date=payload.activity_date.isoformat(), principal=principal)
    async with _session_factory() as session:
        row = ActivityLog(
            user_id=payload.user_id,
            activity_date=payload.activity_date,
            activity_type=payload.activity_type,
            distance_mi=Decimal(str(payload.distance_mi)) if payload.distance_mi is not None else None,
            duration_min=payload.duration_min,
            elevation_ft=payload.elevation_ft,
            avg_hr=payload.avg_hr,
            max_hr=payload.max_hr,
            strain=Decimal(str(payload.strain)) if payload.strain is not None else None,
            source=payload.source,
            notes=payload.notes,
        )
        session.add(row)
        await session.flush()
        await invalidate_cache(session, payload.user_id, date_type.today())
        await session.commit()
        await session.refresh(row)
        return ActivityResponse(
            id=row.id,
            user_id=row.user_id,
            activity_date=row.activity_date,
            activity_type=row.activity_type,
            distance_mi=float(row.distance_mi) if row.distance_mi is not None else None,
            duration_min=row.duration_min,
            elevation_ft=row.elevation_ft,
            avg_hr=row.avg_hr,
            max_hr=row.max_hr,
            strain=float(row.strain) if row.strain is not None else None,
            source=row.source,
            notes=row.notes,
        )
```

- [ ] **Step 4: Register in `main.py`**

Add import `from .routes.activities import router as activities_router` and `app.include_router(activities_router)`.

- [ ] **Step 5: Run to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_routes_activities.py -q`
Expected: PASS (3).

- [ ] **Step 6: Commit**

```bash
git add src/health_metrics/routes/activities.py src/health_metrics/main.py tests/test_routes_activities.py
git commit -m "feat(api): POST /api/v1/activities — structured activity logging"
```

---

### Task 9: `POST /api/v1/body-composition` route

**Files:**
- Create: `src/health_metrics/routes/body_composition.py`
- Modify: `src/health_metrics/main.py`
- Test: `tests/test_routes_body_composition.py`

**Interfaces:**
- Consumes: `BodyComposition` model, `invalidate_cache`.
- Produces: `POST /api/v1/body-composition` → 201 with created row incl. `id`.

- [ ] **Step 1: Write the failing tests**

```python
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from health_metrics.models import BodyComposition


@asynccontextmanager
async def _ctx(db_session):
    yield db_session


@pytest.mark.asyncio
async def test_body_comp_insert(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import body_composition as bc_route
    monkeypatch.setattr(bc_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/body-composition",
            headers={"Authorization": "Bearer dash-tok"},
            json={"user_id": test_user_id, "measured_date": "2026-07-01", "source": "dexa", "lean_mass_lbs": 170.0, "body_fat_pct": 18.5},
        )
    assert resp.status_code == 201
    assert resp.json()["lean_mass_lbs"] == 170.0
    r = await db_session.execute(select(BodyComposition).where(BodyComposition.user_id == test_user_id))
    assert float(r.scalar_one().lean_mass_lbs) == 170.0


@pytest.mark.asyncio
async def test_body_comp_401(db_session, monkeypatch, test_user_id):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    from health_metrics.routes import body_composition as bc_route
    monkeypatch.setattr(bc_route, "_session_factory", lambda: _ctx(db_session))
    from health_metrics.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/body-composition", json={"measured_date": "2026-07-01", "source": "dexa"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_routes_body_composition.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `body_composition.py`** (route; mirror activities.py)

```python
"""POST /api/v1/body-composition — log a DEXA / body-comp reading. Insert-only."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import AsyncSessionLocal
from ..models import BodyComposition
from ..regulation.cache import invalidate_cache
from .auth import Principal, get_principal

log = structlog.get_logger()
router = APIRouter(prefix="/api/v1")


def _session_factory() -> AsyncIterator[AsyncSession]:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            yield session

    return _ctx()


def _dec(v: float | None) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


class BodyCompPayload(BaseModel):
    user_id: str = "hugo"
    measured_date: date_type = Field(default_factory=date_type.today)
    source: str = "dexa"
    weight_lbs: float | None = None
    body_fat_pct: float | None = None
    lean_mass_lbs: float | None = None
    fat_mass_lbs: float | None = None
    notes: str | None = None


class BodyCompResponse(BaseModel):
    id: int
    user_id: str
    measured_date: date_type
    source: str
    weight_lbs: float | None
    body_fat_pct: float | None
    lean_mass_lbs: float | None
    fat_mass_lbs: float | None
    notes: str | None


@router.post("/body-composition", response_model=BodyCompResponse, status_code=201)
async def post_body_composition(
    payload: BodyCompPayload,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> BodyCompResponse:
    log.info("body_comp_write", user_id=payload.user_id, measured_date=payload.measured_date.isoformat(), principal=principal)
    async with _session_factory() as session:
        row = BodyComposition(
            user_id=payload.user_id,
            measured_date=payload.measured_date,
            source=payload.source,
            weight_lbs=_dec(payload.weight_lbs),
            body_fat_pct=_dec(payload.body_fat_pct),
            lean_mass_lbs=_dec(payload.lean_mass_lbs),
            fat_mass_lbs=_dec(payload.fat_mass_lbs),
            notes=payload.notes,
        )
        session.add(row)
        await session.flush()
        await invalidate_cache(session, payload.user_id, date_type.today())
        await session.commit()
        await session.refresh(row)
        return BodyCompResponse(
            id=row.id,
            user_id=row.user_id,
            measured_date=row.measured_date,
            source=row.source,
            weight_lbs=float(row.weight_lbs) if row.weight_lbs is not None else None,
            body_fat_pct=float(row.body_fat_pct) if row.body_fat_pct is not None else None,
            lean_mass_lbs=float(row.lean_mass_lbs) if row.lean_mass_lbs is not None else None,
            fat_mass_lbs=float(row.fat_mass_lbs) if row.fat_mass_lbs is not None else None,
            notes=row.notes,
        )
```

- [ ] **Step 4: Register in `main.py`**

Add `from .routes.body_composition import router as body_composition_router` and `app.include_router(body_composition_router)`.

- [ ] **Step 5: Run to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_routes_body_composition.py -q`
Expected: PASS (2).

- [ ] **Step 6: Commit**

```bash
git add src/health_metrics/routes/body_composition.py src/health_metrics/main.py tests/test_routes_body_composition.py
git commit -m "feat(api): POST /api/v1/body-composition — DEXA logging"
```

---

### Task 10: Offline calibration + Strava-notes parser

**Files:**
- Create: `scripts/calibrate_energy.py`
- Create: `src/health_metrics/regulation/strava_notes.py` (the parser — importable + testable)
- Test: `tests/regulation/test_strava_notes.py`

**Interfaces:**
- Produces: `parse_strava_note(note: str) -> dict | None` — returns `{"distance_mi": float, "duration_min": int, "elevation_ft": int|None}` or `None`. Used by both the calibration report and the backfill (operator step).

- [ ] **Step 1: Write the failing test**

```python
from health_metrics.regulation.strava_notes import parse_strava_note


def test_parse_full_strava_fragment():
    got = parse_strava_note("... Strava: 2.72 mi, 55:34, 118 ft ...")
    assert got == {"distance_mi": 2.72, "duration_min": 55, "elevation_ft": 118}


def test_parse_no_elevation():
    got = parse_strava_note("Strava: 2.6 mi, 58:10")
    assert got["distance_mi"] == 2.6
    assert got["duration_min"] == 58
    assert got["elevation_ft"] is None


def test_parse_no_match_returns_none():
    assert parse_strava_note("[DAY-CLOSE] nothing here") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `source .venv/bin/activate && pytest tests/regulation/test_strava_notes.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement `strava_notes.py`**

```python
"""Best-effort parser for 'Strava: X mi, MM:SS, Y ft' fragments in manual_log.notes.

Conservative: returns None unless it finds a clear `Strava: <dist> mi, <mm:ss>`
pattern. Used for the Item #6 backfill of Whoop-missed walk days.
"""

from __future__ import annotations

import re

_RE = re.compile(
    r"Strava:\s*([\d.]+)\s*mi\s*,\s*(\d{1,2}):(\d{2})(?:\s*,\s*([\d,]+)\s*ft)?",
    re.IGNORECASE,
)


def parse_strava_note(note: str) -> dict | None:
    m = _RE.search(note or "")
    if not m:
        return None
    dist = float(m.group(1))
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    elev = int(m.group(4).replace(",", "")) if m.group(4) else None
    return {"distance_mi": dist, "duration_min": minutes + (1 if seconds >= 30 else 0), "elevation_ft": elev}
```

- [ ] **Step 4: Run to verify it passes**

Run: `source .venv/bin/activate && pytest tests/regulation/test_strava_notes.py -q`
Expected: PASS.

- [ ] **Step 5: Write `scripts/calibrate_energy.py`** (offline report; mirror `scripts/validate_water_retention.py` style — a `__main__` script that connects via `DATABASE_URL`, pulls the last N days of `manual_log` (intake+weight), `daily_metrics.whoop_kcal_burned`, workouts + activity_log, computes modeled TDEE across a grid of `(baseline_activity_factor, neat_coef)`, and prints the pair minimizing combined error against `whoop_kcal_burned` and the Kalman `revealed_tdee`. Read-only — prints a recommended `EnergyParams`; does NOT write.)

```python
"""Offline calibration for the energy model. Read-only.

Grid-searches (baseline_activity_factor, neat_coef) to minimize combined error
of modeled TDEE against whoop_kcal_burned AND the Kalman revealed_tdee over
recent history, then prints the recommended constants to paste into
energy_config.py. Run with DATABASE_URL set (public proxy for prod).

    python -m scripts.calibrate_energy --user hugo --days 30
"""
# Implementation: reuse compute_energy_today's fetch logic against a rolling
# window; import kalman revealed_tdee from brief.compute_weight_trend. Print a
# table of (factor, coef) -> RMSE_vs_whoop, RMSE_vs_revealed, combined. No writes.
```

(The engineer fills the body following `scripts/validate_water_retention.py`; it is an operator tool, not import-tested. Keep it read-only.)

- [ ] **Step 6: Commit**

```bash
git add src/health_metrics/regulation/strava_notes.py scripts/calibrate_energy.py tests/regulation/test_strava_notes.py
git commit -m "feat(regulation): strava-note parser + offline energy calibration script"
```

---

### Task 11: CI — add new files to ruff lint scope

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add the new source + test paths to BOTH ruff lines** (the `ruff check` line and the `ruff format --check` line). Append to each:

```
src/health_metrics/routes/activities.py src/health_metrics/routes/body_composition.py tests/test_routes_activities.py tests/test_routes_body_composition.py tests/test_schemas_energy.py
```

(The `tests/regulation/` dir and `scripts/` are already covered by the existing `tests/regulation/` and `scripts/` entries, so `test_energy.py`, `test_energy_config.py`, `test_body_composition.py`, `test_strava_notes.py`, `test_brief_energy.py`, and `calibrate_energy.py` are auto-included.)

- [ ] **Step 2: Verify locally**

Run:
```bash
source .venv/bin/activate
ruff check src/health_metrics/routes/activities.py src/health_metrics/routes/body_composition.py src/health_metrics/regulation/energy.py src/health_metrics/regulation/energy_config.py src/health_metrics/regulation/body_composition.py src/health_metrics/regulation/strava_notes.py tests/test_routes_activities.py tests/test_routes_body_composition.py tests/test_schemas_energy.py tests/regulation/
ruff format --check src/health_metrics/regulation/energy.py src/health_metrics/routes/activities.py
```
Expected: `All checks passed!` and `already formatted`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add activity/body-composition/energy files to ruff scope"
```

---

### Task 12: MCP tools — `log_activity` + `log_body_composition`

**Files:**
- Modify: `~/mcp-unified-server/tools/health_metrics.py`
- Test: `~/mcp-unified-server/tests/test_health_metrics.py`

**Interfaces:**
- Consumes: deployed `POST /api/v1/activities` + `POST /api/v1/body-composition`.
- Produces: two tools mirroring the existing write-wrapper pattern (tool dict in `get_tools()`, branch in `execute()`, async method). `log_activity` defaults `source="manual"`; `log_body_composition` defaults `source="dexa"`. Both default `activity_date`/`measured_date` to today when omitted (send `date.today().isoformat()`).

- [ ] **Step 1: Write failing tests** (mirror `test_set_regulation_override_posts_with_defaults`)

```python
@pytest.mark.asyncio
async def test_log_activity_posts_to_activities():
    tool = HealthMetricsTools(base_url="https://example.com", token="t")
    captured = {}

    async def fake_post(self, url, headers=None, json=None):
        captured["url"] = url
        captured["body"] = json or {}
        resp = MagicMock()
        resp.json.return_value = {"id": 1, "activity_type": "walk"}
        resp.raise_for_status = MagicMock(return_value=None)
        return resp

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        await tool.log_activity(activity_type="walk", distance_mi=2.7, activity_date="2026-07-13")
    assert captured["url"].endswith("/api/v1/activities")
    assert captured["body"]["activity_type"] == "walk"
    assert captured["body"]["source"] == "manual"


@pytest.mark.asyncio
async def test_log_body_composition_posts():
    tool = HealthMetricsTools(base_url="https://example.com", token="t")
    captured = {}

    async def fake_post(self, url, headers=None, json=None):
        captured["url"] = url
        captured["body"] = json or {}
        resp = MagicMock()
        resp.json.return_value = {"id": 1}
        resp.raise_for_status = MagicMock(return_value=None)
        return resp

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        await tool.log_body_composition(lean_mass_lbs=170.0, measured_date="2026-07-01")
    assert captured["url"].endswith("/api/v1/body-composition")
    assert captured["body"]["lean_mass_lbs"] == 170.0
    assert captured["body"]["source"] == "dexa"


def test_activity_and_bodycomp_tools_registered():
    names = [d["name"] for d in HealthMetricsTools("http://x", "y").get_tools()]
    assert "log_activity" in names
    assert "log_body_composition" in names
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/mcp-unified-server && source .venv/bin/activate && python -m pytest tests/test_health_metrics.py -q`
Expected: FAIL (methods/tools missing).

- [ ] **Step 3: Implement** — add two tool dicts to `get_tools()`, two `execute()` branches, and two methods:

```python
    async def log_activity(
        self,
        activity_type: str,
        activity_date: str | None = None,
        distance_mi: float | None = None,
        duration_min: int | None = None,
        elevation_ft: int | None = None,
        avg_hr: int | None = None,
        max_hr: int | None = None,
        strain: float | None = None,
        source: str = "manual",
        notes: str | None = None,
        user_id: str = "hugo",
    ) -> dict:
        if not self.base_url or not self.token:
            raise RuntimeError("HEALTH_API_URL / HEALTH_API_TOKEN_MCP not set")
        body: dict[str, Any] = {
            "user_id": user_id,
            "activity_date": activity_date if activity_date is not None else date.today().isoformat(),
            "activity_type": activity_type,
            "source": source,
        }
        for k, v in (
            ("distance_mi", distance_mi), ("duration_min", duration_min), ("elevation_ft", elevation_ft),
            ("avg_hr", avg_hr), ("max_hr", max_hr), ("strain", strain), ("notes", notes),
        ):
            if v is not None:
                body[k] = v
        url = f"{self.base_url}/api/v1/activities"
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()

    async def log_body_composition(
        self,
        measured_date: str | None = None,
        source: str = "dexa",
        weight_lbs: float | None = None,
        body_fat_pct: float | None = None,
        lean_mass_lbs: float | None = None,
        fat_mass_lbs: float | None = None,
        notes: str | None = None,
        user_id: str = "hugo",
    ) -> dict:
        if not self.base_url or not self.token:
            raise RuntimeError("HEALTH_API_URL / HEALTH_API_TOKEN_MCP not set")
        body: dict[str, Any] = {
            "user_id": user_id,
            "measured_date": measured_date if measured_date is not None else date.today().isoformat(),
            "source": source,
        }
        for k, v in (
            ("weight_lbs", weight_lbs), ("body_fat_pct", body_fat_pct),
            ("lean_mass_lbs", lean_mass_lbs), ("fat_mass_lbs", fat_mass_lbs), ("notes", notes),
        ):
            if v is not None:
                body[k] = v
        url = f"{self.base_url}/api/v1/body-composition"
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()
```

Tool dicts (add to `get_tools()` list): `log_activity` — inputSchema with `activity_type` (enum walk/run/ride/z2/hiit/strength/climb/other, required), `activity_date`, `distance_mi`, `duration_min`, `elevation_ft`, `avg_hr`, `max_hr`, `strain`, `source` (enum strava/whoop/peloton/manual/api), `notes`, `user_id`; `required: ["activity_type"]`. `log_body_composition` — `measured_date`, `source` (enum dexa/bioimpedance/hydrostatic/manual), `weight_lbs`, `body_fat_pct`, `lean_mass_lbs`, `fat_mass_lbs`, `notes`, `user_id`. Add both names to `execute()`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/mcp-unified-server && source .venv/bin/activate && python -m pytest tests/test_health_metrics.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** (on a branch `feat/activity-energy-tools`)

```bash
cd ~/mcp-unified-server && git checkout -b feat/activity-energy-tools
git add tools/health_metrics.py tests/test_health_metrics.py
git commit -m "feat(tools): log_activity + log_body_composition MCP tools"
```

---

### Task 13: MCP re-vendor types (picks up `EnergyToday`)

**Files:**
- Modify: `~/mcp-unified-server/tools/health_metrics_types.py`
- Test: `~/mcp-unified-server/tests/test_health_metrics.py`

- [ ] **Step 1: Write a failing shape-contract test**

```python
@pytest.mark.asyncio
async def test_brief_with_energy_today_round_trips():
    tool = HealthMetricsTools(base_url="https://example.com", token="t")
    fixture = _fixture_brief_json()
    fixture["energy_today"] = {
        "neat_kcal": 120.0, "baseline_kcal": 2700, "rmr_kcal": 2000,
        "tdee_measured_kcal": 2650, "tdee_modeled_kcal": 2820,
        "tdee_estimate_kcal": 2820, "divergence_flag": False,
        "activities_counted": ["walk 2.7mi (activity_log)"], "rmr_source": "dexa",
    }

    async def fake_get(self, url, headers=None, params=None):
        resp = MagicMock()
        resp.json.return_value = fixture
        resp.raise_for_status = MagicMock(return_value=None)
        return resp

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        result = await tool.get_session_brief("hugo")
    brief = SessionBrief.model_validate(result)
    assert brief.energy_today.tdee_estimate_kcal == 2820
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/mcp-unified-server && source .venv/bin/activate && python -m pytest tests/test_health_metrics.py::test_brief_with_energy_today_round_trips -q`
Expected: FAIL (`EnergyToday` not in vendored schema; `brief.energy_today` is None/unknown).

- [ ] **Step 3: Re-vendor** (after Task 5 is merged to health-metrics main)

```bash
cd ~/mcp-unified-server
NEWSHA=$(cd ~/code/health-metrics-service && git rev-parse --short HEAD)
cp ~/code/health-metrics-service/src/health_metrics/regulation/schemas.py tools/health_metrics_types.py
# then replace the module docstring with the vendoring header carrying $NEWSHA
```
Restore the vendoring header docstring (as in the existing file) with `Source: growthink1/health-metrics-service @ <NEWSHA>`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/mcp-unified-server && source .venv/bin/activate && python -m pytest tests/test_health_metrics.py -q`
Expected: PASS (all, incl. the new energy round-trip).

- [ ] **Step 5: Commit + PR + merge**

```bash
git add tools/health_metrics_types.py tests/test_health_metrics.py
git commit -m "chore(tools): re-vendor SessionBrief @ <NEWSHA> — EnergyToday"
git push -u origin feat/activity-energy-tools
gh pr create --title "feat(tools): activity + body-composition tools + EnergyToday re-vendor" --body "..."
gh pr merge --squash --delete-branch
```

---

## Deployment & backfill (operator — run in the main session, NOT a subagent)

After all backend tasks merge to `health-metrics-service` main and CI is green:

1. **Prod migrate** (before code deploy — brief tolerates empty tables): set `DATABASE_URL` to the public proxy (`postgresql+asyncpg://…@caboose.proxy.rlwy.net:PORT/railway`), `alembic upgrade head`, verify `activity_log` + `body_composition` exist.
2. **Deploy code:** `railway up --detach`; poll to SUCCESS.
3. **Smoke-test** via `backend-production-44b0.up.railway.app`: `POST /api/v1/body-composition` (Hugo's DEXA), `POST /api/v1/activities` (a walk), then `GET /api/v1/session-brief?user_id=hugo` and confirm `energy_today` populated (neat_kcal, tdee_modeled/measured, divergence_flag).
4. **Backfill (show before insert):**
   - Query `manual_log.notes` Jul 6–13; run `parse_strava_note` on each; **only** insert `activity_log` rows for days with **no** Whoop `walking` workout (Jul 8/11/12/13 candidates). Print the parsed rows and get Hugo's confirmation before inserting.
   - Insert Hugo's known DEXA reading into `body_composition` (from the Jul 8 note / his DEXA) — surface the parsed lean-mass/body-fat for confirmation first.
5. **Invalidate** hugo's `regulation_cache` rows (deploys don't auto-bust — see the separate chip) and re-verify the live brief.
6. **Calibrate:** run `python -m scripts.calibrate_energy --user hugo --days 30` against prod (read-only); if the recommended `(baseline_activity_factor, neat_coef)` differ materially from the seeds, update `energy_config.py`, re-run the energy tests, and ship a follow-up commit.
7. **MCP restart:** the local `python -m src.main` server is spawned by Claude Desktop — tell Hugo to quit/reopen Claude Desktop (or toggle the connector) so the 2 new tools + `energy_today` are visible.

---

## Self-Review

- **Spec coverage:** Part A (activity_log + log_activity + backfill) → Tasks 1,2,8,10,12 + operator §4. Part A2 (body_composition + DEXA + Katch-McArdle) → Tasks 1,2,3,9,12. Part B (energy.py, config, blend, calibration, validator) → Tasks 3,4,5,6,7,10. Surfacing (EnergyToday) → Tasks 5,7. MCP → Tasks 12,13. Testing → each task's tests + operator smoke. Deploy → operator section. ✅ all spec sections mapped.
- **Placeholder scan:** `calibrate_energy.py` body is intentionally operator-authored (read-only report tool, not import-tested) — flagged, not a silent gap. All other steps carry complete code.
- **Type consistency:** `Activity(activity_type, source_layer, distance_mi, duration_min, kcal)`, `compute_energy(rmr_kcal, rmr_source, weight_lbs, activities, whoop_kcal_burned, whoop_complete, params)`, `EnergyToday{neat_kcal, baseline_kcal, rmr_kcal, tdee_measured_kcal, tdee_modeled_kcal, tdee_estimate_kcal, divergence_flag, activities_counted, rmr_source}`, `katch_mcardle_rmr(lean_mass_lbs) -> int`, `compute_energy_today(session, user_id, as_of, weight_lbs, today)` — consistent across Tasks 5/6/7/12/13.
