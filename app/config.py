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

    # Email channel: poll an IMAP mailbox for submitted incidents (every
    # email_poll_minutes, deterministic UID cursor — the LLM only ever sees
    # genuinely new messages) and send write-ups out via SMTP. The channel is
    # active when email_enabled is true AND the IMAP settings below are filled
    # in; nothing is sent until a digest recipient is configured (env below, or
    # the Settings page).
    email_enabled: bool = True
    imap_host: str = ""
    imap_port: int = 993                       # SSL
    imap_username: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    smtp_host: str = ""                        # "" -> derived from imap_host
    smtp_port: int = 465                       # 465 = SSL, 587 = STARTTLS
    smtp_username: str = ""                    # "" -> imap_username
    smtp_password: str = ""                    # "" -> imap_password
    mail_from: str = ""                        # "" -> smtp/imap username
    digest_to: str = ""                        # write-up recipient (Settings page overrides)
    email_poll_minutes: int = 5
    email_max_per_poll: int = 10               # LLM backpressure; rest wait for next poll
    email_max_bytes: int = 200_000             # body text considered per message
    email_max_attempts: int = 3                # tries before a message is parked as error
    aiid_check_cron: str = "0 12 * * *"        # daily AIID check, in aiid_check_tz
    aiid_check_tz: str = "America/New_York"    # noon Washington DC, DST-aware
    aiid_digest_max: int = 15                  # write-ups per digest; the rest roll over
    email_classify_model: str = ""             # "" -> anthropic_confirm_model
    writeup_effort: str = "high"               # research + compose effort
    writeup_max_tokens: int = 8000             # output cap for the compose step
    research_max_tokens: int = 6000            # output cap for the web-search step
    web_search_max_uses: int = 5               # searches allowed per research call

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
    table_rows: int = 12                               # incident rows per issue
    row_sources_max: int = 3                           # source links rendered per row

    @property
    def resolved_screen_model(self) -> str:
        """Model used for the per-incident privacy screen (defaults to the writer)."""
        return self.anthropic_screen_model or self.anthropic_model

    @property
    def resolved_email_classify_model(self) -> str:
        """Cheap model that reads new inbound email (defaults to the confirm model)."""
        return self.email_classify_model or self.anthropic_confirm_model

    @property
    def resolved_smtp_host(self) -> str:
        """SMTP host, derived from the IMAP host when unset (imap.x -> smtp.x)."""
        if self.smtp_host:
            return self.smtp_host
        if self.imap_host.startswith("imap."):
            return "smtp." + self.imap_host[len("imap."):]
        return self.imap_host

    @property
    def resolved_smtp_username(self) -> str:
        return self.smtp_username or self.imap_username

    @property
    def resolved_smtp_password(self) -> str:
        return self.smtp_password or self.imap_password

    @property
    def resolved_mail_from(self) -> str:
        return self.mail_from or self.resolved_smtp_username

    @property
    def email_configured(self) -> bool:
        """Whether the email channel has enough config to run its jobs."""
        return bool(
            self.email_enabled
            and self.imap_host
            and self.imap_username
            and self.imap_password
        )

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
