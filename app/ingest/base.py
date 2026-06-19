"""Source connector interface shared by all ingestion sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class RawItem:
    """One ingested record, normalized across sources."""

    source: str
    external_id: str
    title: str = ""
    description: str = ""
    url: str = ""
    date: str = ""                                   # ISO date string
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        return f"{self.source}:{self.external_id}"


@dataclass
class FetchResult:
    """Items fetched plus the connector's new cursor position."""

    items: list[RawItem]
    cursor: str
    note: str = ""


@runtime_checkable
class SourceConnector(Protocol):
    """A pluggable ingestion source.

    ``fetch`` is given the last stored cursor and returns the items to upsert
    plus a new cursor. Connectors may return already-seen items (the runner
    upserts idempotently) so that late-arriving metadata is picked up.
    """

    kind: str

    def fetch(self, cursor: str) -> FetchResult: ...
