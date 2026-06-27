"""Application configuration, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Signing keys that must never be used in production — the in-repo defaults.
WEAK_SECRET_KEYS = {"", "change-me", "change-me-in-production"}
MIN_SECRET_KEY_LEN = 32


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

    # Scheduler. Off by default: the app runs on demand from the web UI
    # (Sources → "Run ingest now", Newsletters → "Generate"). Set
    # APN_SCHEDULER_ENABLED=true to also run the cron jobs below automatically.
    scheduler_enabled: bool = False
    daily_ingest_cron: str = "0 7 * * *"      # 07:00 daily
    monthly_synth_cron: str = "0 8 1 * *"     # 08:00 on the 1st

    # LLM (used for monthly synthesis only in v1).
    anthropic_api_key: str = ""
    anthropic_monthly_budget_usd: float = 100.0
    anthropic_model: str = "claude-opus-4-8"          # synthesis / writing
    anthropic_confirm_model: str = "claude-haiku-4-5"  # cheap member-flag confirm
    anthropic_screen_model: str = ""                   # privacy screen; "" -> anthropic_model
    synth_effort: str = "high"                         # low|medium|high|xhigh|max
    synth_max_tokens: int = 32000                      # output cap for the draft (streamed)
    screen_effort: str = "medium"                      # effort for the per-incident screen
    screen_max_tokens: int = 12000                     # output cap for the screen pass
    featured_count: int = 4                            # featured stories per issue
    brief_count: int = 8                               # brief mentions per issue

    @property
    def resolved_screen_model(self) -> str:
        """Model used for the per-incident privacy screen (defaults to the writer)."""
        return self.anthropic_screen_model or self.anthropic_model

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_dir.rstrip('/')}/digest.db"

    @model_validator(mode="after")
    def _require_strong_secret_in_production(self) -> "Settings":
        """Refuse to boot in production with a known/weak/short signing key.

        Starlette session cookies are signed (not encrypted) with secret_key, and
        the session holds the admin's user_id, so a known key means forgeable
        sessions and full auth bypass. Fail loudly at startup instead.
        """
        if self.environment == "production":
            if (
                self.secret_key in WEAK_SECRET_KEYS
                or len(self.secret_key) < MIN_SECRET_KEY_LEN
            ):
                raise ValueError(
                    "APN_SECRET_KEY must be a strong random value of at least "
                    f"{MIN_SECRET_KEY_LEN} characters in production. Generate one with: "
                    'python -c "import secrets; print(secrets.token_urlsafe(48))"'
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
