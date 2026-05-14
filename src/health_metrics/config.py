"""Configuration loaded from environment variables."""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        ...,
        description="Async SQLAlchemy connection string, e.g. postgresql+asyncpg://user:pw@host/db",
    )

    # Identity & locale
    user_id: str = Field(default="hugo")
    timezone: str = Field(default="America/New_York")

    # Logging
    log_level: str = Field(default="INFO")

    # Oura
    oura_personal_token: Optional[str] = Field(default=None)
    oura_base_url: str = Field(default="https://api.ouraring.com/v2")

    # Whoop
    whoop_client_id: Optional[str] = Field(default=None)
    whoop_client_secret: Optional[str] = Field(default=None)
    whoop_refresh_token: Optional[str] = Field(default=None)
    whoop_redirect_uri: str = Field(default="http://localhost:8000/whoop/callback")
    whoop_base_url: str = Field(default="https://api.prod.whoop.com/developer/v1")
    whoop_oauth_url: str = Field(default="https://api.prod.whoop.com/oauth/oauth2/token")

    # Anthropic (dashboard narration)
    anthropic_api_key: Optional[str] = Field(default=None)
    narration_model: str = Field(default="claude-3-5-haiku-latest")
    narration_max_tokens: int = Field(default=80)

    # CORS (dashboard frontend)
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
