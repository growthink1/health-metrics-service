from health_metrics.config import get_settings


def test_settings_loads_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("USER_ID", "testuser")
    get_settings.cache_clear()
    s = get_settings()
    assert s.database_url == "postgresql+asyncpg://test:test@localhost/test"
    assert s.user_id == "testuser"


def test_settings_defaults_timezone_and_log_level(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    get_settings.cache_clear()
    s = get_settings()
    assert s.timezone == "America/New_York"
    assert s.log_level == "INFO"


def test_settings_anthropic_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    get_settings.cache_clear()
    s = get_settings()
    assert s.narration_model == "claude-3-5-haiku-latest"
    assert s.narration_max_tokens == 80
    assert s.cors_allowed_origins == ["http://localhost:3000"]
