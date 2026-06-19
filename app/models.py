"""Database models.

Phase 0 establishes a couple of foundational tables. Domain tables (incidents,
members, newsletters, …) are added in later phases.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AppUser(SQLModel, table=True):
    """The single admin user (the design assumes one operator)."""

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Setting(SQLModel, table=True):
    """Simple key/value application settings, editable from the admin UI."""

    key: str = Field(primary_key=True)
    value: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class Source(SQLModel, table=True):
    """An ingestion source (one connector). v1 ships a single AIID source."""

    name: str = Field(primary_key=True)        # e.g. "aiid"
    kind: str = ""                             # connector kind, e.g. "aiid_snapshot"
    enabled: bool = True
    cursor: str = ""                           # opaque per-connector position
    last_run: datetime | None = None
    last_status: str = ""                      # ok | error
    last_detail: str = ""


class TaxonomyCategory(SQLModel, table=True):
    """A privacy-incident taxonomy category (editable from the admin UI)."""

    key: str = Field(primary_key=True)        # stable slug, e.g. "pii_leakage"
    label: str = ""
    description: str = ""
    enabled: bool = True
    sort_order: int = 0


class Incident(SQLModel, table=True):
    """An ingested incident, plus classification once it has run.

    For the single-source v1 we keep raw ingestion and classification on one
    row (with the full source payload retained in ``raw_payload``). The
    ``source``/``external_id``/``dedup_key`` columns keep multi-source open.
    """

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    external_id: str = Field(index=True)
    dedup_key: str = Field(index=True, unique=True)

    title: str = ""
    description: str = ""
    url: str = ""
    incident_date: str = ""                    # ISO date string as provided
    raw_payload: str = "{}"                    # JSON of the source record

    # Classification (filled by the classifier in Phase 3).
    status: str = Field(default="pending", index=True)  # pending|classified|not_privacy|needs_review
    is_privacy: bool = False
    categories: str = "[]"                     # JSON list of taxonomy category keys
    confidence: float = 0.0
    decided_by: str = ""                       # e.g. "aiid_tags:MIT"
    classified_at: datetime | None = None

    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)


class Run(SQLModel, table=True):
    """A record of a scheduled or manual job execution."""

    id: int | None = Field(default=None, primary_key=True)
    job: str = Field(index=True)          # e.g. "daily_ingest", "monthly_synth"
    status: str = "started"               # started | ok | error
    detail: str = ""
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
