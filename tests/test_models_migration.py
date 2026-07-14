from health_metrics.models import Base


def test_all_expected_tables_registered():
    tables = {t.name for t in Base.metadata.sorted_tables}
    assert tables == {
        "daily_metrics",
        "workouts",
        "manual_log",
        "regulation_recommendations",
        "oauth_state",
        "narration_cache",
        "meals",
        "workout_sets",
        "goals",
        "milestones",
        "subgoals",
        "goal_recommendations",
        "health_events",
        "regulation_cache",
        "regulation_overrides",
        "activity_log",
        "body_composition",
    }


def test_daily_metrics_unique_constraint():
    t = Base.metadata.tables["daily_metrics"]
    uqs = {c.name for c in t.constraints if c.__class__.__name__ == "UniqueConstraint"}
    assert "uq_daily_metrics_user_date" in uqs


def test_workouts_source_uniqueness():
    t = Base.metadata.tables["workouts"]
    uqs = {c.name for c in t.constraints if c.__class__.__name__ == "UniqueConstraint"}
    assert "uq_workouts_source_sourceid" in uqs


def test_narration_cache_unique_constraint():
    t = Base.metadata.tables["narration_cache"]
    uqs = {c.name for c in t.constraints if c.__class__.__name__ == "UniqueConstraint"}
    assert "uq_narration_cache_user_date_hash" in uqs


def test_health_event_status_check_constraint():
    t = Base.metadata.tables["health_events"]
    checks = {c.name for c in t.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert "health_events_status_check" in checks
    assert "health_events_event_type_check" in checks


def test_regulation_cache_composite_pk():
    t = Base.metadata.tables["regulation_cache"]
    pk_cols = [c.name for c in t.primary_key.columns]
    assert pk_cols == ["user_id", "as_of_date"]


def test_regulation_overrides_check_constraints():
    t = Base.metadata.tables["regulation_overrides"]
    checks = {c.name for c in t.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert "regulation_overrides_field_check" in checks
    assert "regulation_overrides_created_by_check" in checks


def test_activity_log_check_constraints():
    t = Base.metadata.tables["activity_log"]
    checks = {c.name for c in t.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert "activity_log_type_check" in checks
    assert "activity_log_source_check" in checks


def test_body_composition_check_constraint():
    t = Base.metadata.tables["body_composition"]
    checks = {c.name for c in t.constraints if c.__class__.__name__ == "CheckConstraint"}
    assert "body_composition_source_check" in checks
