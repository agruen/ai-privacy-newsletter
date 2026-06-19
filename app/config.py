"""Application configuration, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core
    app_name: str = "AI Privacy Incident Digest"
    environment: str = "development"
    secret_key: str = "change-me-in-production"

    # Storage — directory is mounted as a volume in Docker.
    data_dir: str = "/data"
    database_url: str = ""  # derived from data_dir if empty

    # Admin bootstrap. On first run, if no user exists and these are set,
    # the single admin account is created from them.
    admin_username: str = "admin"
    admin_password: str = ""

    # Scheduler (cron expressions; see APScheduler CronTrigger).
    scheduler_enabled: bool = True
    daily_ingest_cron: str = "0 7 * * *"      # 07:00 daily
    monthly_synth_cron: str = "0 8 1 * *"     # 08:00 on the 1st

    # LLM (used for monthly synthesis only in v1).
    anthropic_api_key: str = ""
    anthropic_monthly_budget_usd: float = 100.0
    anthropic_model: str = "claude-opus-4-8"          # synthesis / writing
    anthropic_confirm_model: str = "claude-haiku-4-5"  # cheap member-flag confirm
    synth_effort: str = "high"                         # low|medium|high|xhigh|max
    featured_count: int = 4                            # featured stories per issue
    brief_count: int = 8                               # brief mentions per issue

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_dir.rstrip('/')}/digest.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
