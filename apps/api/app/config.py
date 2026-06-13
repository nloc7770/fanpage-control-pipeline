"""Application configuration via pydantic-settings.

All runtime knobs are read from environment variables (with .env support). The
canonical list of variables lives in the repo root `.env.example`; every var
referenced by the API service is typed here.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings for the API service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Database ----------------------------------------------------------
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://factory:factory@postgres:5432/factory"
    )

    # -- Redis -------------------------------------------------------------
    REDIS_URL: str = Field(default="redis://redis:6379/0")
    CELERY_BROKER_URL: str = Field(default="redis://redis:6379/1")
    CELERY_RESULT_BACKEND: str = Field(default="redis://redis:6379/2")

    # -- HTTP --------------------------------------------------------------
    API_PORT: int = Field(default=8080)
    CORS_ORIGINS: str = Field(default="http://localhost:3000")

    # -- Logging -----------------------------------------------------------
    LOG_LEVEL: str = Field(default="INFO")

    # -- Storage -----------------------------------------------------------
    STORAGE_BACKEND: str = Field(default="local")
    STORAGE_LOCAL_PATH: str = Field(default="/data/storage")
    S3_ENDPOINT_URL: str | None = Field(default=None)
    S3_REGION: str | None = Field(default=None)
    S3_BUCKET: str | None = Field(default=None)
    S3_ACCESS_KEY_ID: str | None = Field(default=None)
    S3_SECRET_ACCESS_KEY: str | None = Field(default=None)
    S3_FORCE_PATH_STYLE: int = Field(default=1)

    # -- Mock toggles (pass-through context for workers; API ignores) ------
    MOCK_DOWNLOAD: int = Field(default=0)
    MOCK_ASR: int = Field(default=0)
    MOCK_LLM: int = Field(default=0)
    MOCK_RENDER: int = Field(default=0)
    MOCK_YOLO: int = Field(default=0)

    # -- Behavioural toggles ----------------------------------------------
    ENABLE_DIARIZATION: int = Field(default=1)
    DUPLICATE_JOB_WINDOW_S: int = Field(default=60)

    # -- Facebook integration ----------------------------------------------
    FACEBOOK_APP_ID: str | None = Field(default=None)
    FACEBOOK_APP_SECRET: str | None = Field(default=None)
    FACEBOOK_REDIRECT_URI: str = Field(
        default="http://localhost:8080/auth/facebook/callback"
    )
    FACEBOOK_GRAPH_API_VERSION: str = Field(default="v22.0")
    FACEBOOK_DAILY_LIMIT_PER_PAGE: int = Field(default=10)
    FACEBOOK_MIN_DELAY_BETWEEN_POSTS_S: int = Field(default=1800)
    REQUIRE_MANUAL_APPROVAL: bool = Field(default=True)
    TOKEN_ENCRYPTION_KEY: str | None = Field(default=None)

    # -- YouTube discovery -------------------------------------------------
    YOUTUBE_DISCOVERY_MODE: str = Field(default="yt_dlp")
    YOUTUBE_API_KEY: str | None = Field(default=None)
    YOUTUBE_MAX_RESULTS_PER_PAGE: int = Field(default=10)
    YOUTUBE_MIN_DURATION_SECONDS: int = Field(default=180)
    YOUTUBE_MAX_DURATION_SECONDS: int = Field(default=1800)

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Used as a FastAPI dependency."""
    return Settings()
