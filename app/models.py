"""Database models.

Phase 0 establishes a couple of foundational tables. Domain tables (incidents,
members, newsletters, …) are added in later phases.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Setting(SQLModel, table=True):
    """Simple key/value application settings, editable from the admin UI."""

    key: str = Field(primary_key=True)
    value: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class Run(SQLModel, table=True):
    """A record of a scheduled or manual job execution."""

    id: int | None = Field(default=None, primary_key=True)
    job: str = Field(index=True)          # e.g. "daily_ingest", "monthly_synth"
    status: str = "started"               # started | ok | error
    detail: str = ""
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
