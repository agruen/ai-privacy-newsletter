"""The ingest runner: pull from each enabled source and upsert incidents."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlmodel import Session, select

from app.ingest.aiid import AIIDSnapshotConnector
from app.ingest.base import RawItem, SourceConnector
from app.models import Incident, Source, utcnow

logger = logging.getLogger(__name__)

# Connector registry: kind -> factory(source_name).
CONNECTORS = {
    AIIDSnapshotConnector.kind: lambda name: AIIDSnapshotConnector(source_name=name),
}


@dataclass
class IngestSummary:
    source: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    note: str = ""
    error: str = ""


def build_connector(source: Source) -> SourceConnector:
    factory = CONNECTORS.get(source.kind)
    if factory is None:
        raise ValueError(f"unknown connector kind: {source.kind!r}")
    return factory(source.name)


def _upsert(session: Session, item: RawItem) -> bool:
    """Insert or update an incident. Returns True if newly created."""
    existing = session.exec(
        select(Incident).where(Incident.dedup_key == item.dedup_key)
    ).first()
    payload = json.dumps(item.payload, default=str)
    if existing is None:
        session.add(
            Incident(
                source=item.source,
                external_id=item.external_id,
                dedup_key=item.dedup_key,
                title=item.title,
                description=item.description,
                url=item.url,
                incident_date=item.date,
                raw_payload=payload,
                status="pending",
            )
        )
        return True
    existing.title = item.title
    existing.description = item.description
    existing.url = item.url
    existing.incident_date = item.date
    existing.raw_payload = payload
    existing.last_seen = utcnow()
    session.add(existing)
    return False


def ingest_source(session: Session, source: Source) -> IngestSummary:
    summary = IngestSummary(source=source.name)
    try:
        connector = build_connector(source)
        result = connector.fetch(source.cursor)
        summary.fetched = len(result.items)
        summary.note = result.note
        for item in result.items:
            if _upsert(session, item):
                summary.created += 1
            else:
                summary.updated += 1
        source.cursor = result.cursor
        source.last_status = "ok"
        source.last_detail = (
            f"{summary.note}; +{summary.created} new, {summary.updated} updated"
        )
    except Exception as exc:  # keep one bad source from killing the run
        logger.exception("ingest failed for source %s", source.name)
        summary.error = str(exc)
        source.last_status = "error"
        source.last_detail = str(exc)
    finally:
        source.last_run = utcnow()
        session.add(source)
        session.commit()
    return summary


def run_ingest(session: Session) -> list[IngestSummary]:
    sources = session.exec(select(Source).where(Source.enabled == True)).all()  # noqa: E712
    return [ingest_source(session, s) for s in sources]
