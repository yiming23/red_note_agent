"""Centralized config — all environment variables and runtime settings.

DESIGN.md § 14 invariant: "配置只从 config/settings.py 读，不允许散落 os.getenv".

Usage:
    from xhs_agent.config.settings import settings
    print(settings.anthropic_api_key)

The first import loads .env (via pydantic-settings). Subsequent imports return
the cached singleton.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """All runtime configuration. Reads from .env + environment variables."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 忽略 .env 里多余的 key，方便实验
    )

    # ---- Environment ----
    env: str = Field(default="local", description="local | production")
    log_level: str = Field(default="INFO")

    # ---- Storage ----
    database_url: str = Field(
        default="sqlite:///./xhs_agent.db",
        description="SQLAlchemy URL. SQLite locally, Postgres on server.",
    )

    # ---- LLM ----
    anthropic_api_key: str = Field(default="", description="Required for V0")
    daily_budget_usd: float = Field(default=2.0)

    # Model selection (DESIGN.md § 10)
    model_signal_judgment: str = Field(default="claude-haiku-4-5")
    model_trend_extraction: str = Field(default="claude-haiku-4-5")
    model_translation: str = Field(default="claude-haiku-4-5")
    model_copywriting: str = Field(default="claude-sonnet-4-6")
    model_rewrite: str = Field(default="claude-sonnet-4-6")

    # ---- Telegram ----
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="", description="Yiming's personal chat id")
    telegram_push_enabled: bool = Field(default=True)
    telegram_dry_run: bool = Field(default=False, description="Log only, don't send")

    # ---- IsThereAnyDeal (S8 折扣值不值) ----
    itad_api_key: str = Field(default="", description="ITAD API key — from isthereanydeal.com/dev/app/")

    # ---- Reddit (V1) ----
    reddit_client_id: str = Field(default="")
    reddit_client_secret: str = Field(default="")
    reddit_user_agent: str = Field(default="xhs_game_agent/0.1")

    # ---- Image gen (V1) ----
    fal_api_key: str = Field(default="")

    # ---- Scheduling ----
    trend_pipeline_hour: int = Field(default=9)
    content_pipeline_hours: str = Field(
        default="9,17",
        description="Comma-separated 24h hours, e.g. '9,17' for 9:30 and 17:30",
    )

    # ---- Paths ----
    data_dir: Path = Field(default=PROJECT_ROOT / "data")
    exemplar_screenshots_dir: Path = Field(
        default=PROJECT_ROOT / "exemplar_screenshots"
    )

    # ---- Validators ----
    @field_validator("content_pipeline_hours")
    @classmethod
    def _check_hours(cls, v: str) -> str:
        for part in v.split(","):
            hour = int(part.strip())
            if not 0 <= hour <= 23:
                raise ValueError(f"Invalid hour: {hour}")
        return v

    @property
    def content_pipeline_hour_list(self) -> list[int]:
        return [int(p.strip()) for p in self.content_pipeline_hours.split(",")]

    # ---- Capability checks ----
    def is_v0_ready(self) -> tuple[bool, list[str]]:
        """Return (ready, missing_keys) for V0 minimum to run."""
        missing = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.telegram_chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        return (not missing, missing)

    def is_trend_ready(self) -> tuple[bool, list[str]]:
        """V1 trend pipeline requires Reddit credentials."""
        missing = []
        if not self.reddit_client_id:
            missing.append("REDDIT_CLIENT_ID")
        if not self.reddit_client_secret:
            missing.append("REDDIT_CLIENT_SECRET")
        return (not missing, missing)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton.

    Use this everywhere; do not instantiate Settings() directly.
    """
    s = Settings()
    # Ensure data directories exist
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.exemplar_screenshots_dir.mkdir(parents=True, exist_ok=True)
    return s


# Convenience module-level instance
settings = get_settings()
