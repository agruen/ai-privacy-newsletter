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


class Member(SQLModel, table=True):
    """An FPF member to watch for in drafts (so we can flag mentions)."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    aliases: str = "[]"        # JSON list of alternate names
    notes: str = ""
    created_at: datetime = Field(default_factory=utcnow)


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

    # LLM privacy screening (filled at generation time by app/synth/screen.py).
    # Unlike the AIID tag above, this is judged for *every* incident in the month
    # so the newsletter can surface privacy angles the source never tagged.
    llm_screened: bool = False
    llm_privacy_angle: bool = False            # LLM found a privacy angle
    llm_salience: str = ""                     # high | medium | low (newsletter-worthiness)
    llm_privacy_note: str = ""                 # one-line description of the angle
    llm_screened_at: datetime | None = None
    llm_screen_model: str = ""

    # Single-incident write-up (email channel, app/mail). Cached on the incident
    # so a failed send can be retried without re-billing the research/compose.
    writeup_json: str = "{}"                   # structured write-up sections
    writeup_model: str = ""
    writeup_at: datetime | None = None
    # When the email channel delivered (or deliberately suppressed) this
    # incident. NULL means the daily AIID check still owes it a decision.
    notified_at: datetime | None = Field(default=None, index=True)

    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)


class EmailMessage(SQLModel, table=True):
    """An email received on the intake mailbox, whatever became of it.

    Every message the poller sees gets a row — including bounces and spam that
    were skipped without spending a model call — so the Activity page shows the
    operator everything that arrived and what the app decided about it.
    """

    id: int | None = Field(default=None, primary_key=True)
    uid: int = Field(index=True)               # IMAP UID within the folder
    folder: str = "INBOX"
    message_id: str = Field(default="", index=True)  # RFC 5322 Message-ID
    from_addr: str = ""
    subject: str = ""
    sent_at: str = ""                          # Date header as ISO, best-effort
    size: int = 0                              # raw message bytes
    body_excerpt: str = ""                     # truncated plain text, for review
    # received | skipped | not_incident | processed | error
    status: str = Field(default="received", index=True)
    detail: str = ""                           # human-readable reason/outcome
    attempts: int = 0                          # processing tries (poison guard)
    incident_id: int | None = Field(default=None, foreign_key="incident.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class OutboundEmail(SQLModel, table=True):
    """An email the app sent: a single-incident write-up or the daily digest."""

    id: int | None = Field(default=None, primary_key=True)
    kind: str = ""                             # incident | digest
    to_addr: str = ""
    subject: str = ""
    body_excerpt: str = ""                     # first part of the text body
    incident_ids: str = "[]"                   # JSON list of covered incident ids
    status: str = "sent"                       # sent | error
    detail: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Newsletter(SQLModel, table=True):
    """A monthly draft newsletter (structured content stored as JSON)."""

    id: int | None = Field(default=None, primary_key=True)
    period: str = Field(index=True)            # "YYYY-MM"
    status: str = Field(default="draft", index=True)  # draft | approved
    content_json: str = "{}"                   # structured sections
    note: str = ""                             # generation note
    error: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    approved_at: datetime | None = None


class NewsletterItem(SQLModel, table=True):
    """An incident's role within a newsletter issue."""

    id: int | None = Field(default=None, primary_key=True)
    newsletter_id: int = Field(index=True, foreign_key="newsletter.id")
    incident_id: int = Field(foreign_key="incident.id")
    role: str = "pool"                         # featured | brief | pool
    rank: int = 0
    score: float = 0.0


class MemberFlag(SQLModel, table=True):
    """A flagged mention of an FPF member in a draft."""

    id: int | None = Field(default=None, primary_key=True)
    newsletter_id: int = Field(index=True, foreign_key="newsletter.id")
    member_id: int | None = Field(default=None, foreign_key="member.id")
    member_name: str = ""
    term: str = ""
    section: str = ""
    snippet: str = ""
    confirmed: bool | None = None              # None = not LLM-confirmed
    note: str = ""


class LLMUsage(SQLModel, table=True):
    """One LLM API call's token usage and computed cost."""

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    purpose: str = ""                          # synthesis | member_confirm
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    newsletter_id: int | None = None


class Run(SQLModel, table=True):
    """A record of a scheduled or manual job execution."""

    id: int | None = Field(default=None, primary_key=True)
    job: str = Field(index=True)          # e.g. "daily_ingest", "monthly_synth"
    status: str = "started"               # started | ok | error
    detail: str = ""
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
