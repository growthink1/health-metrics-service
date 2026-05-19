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
