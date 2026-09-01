"""Backfill ``report_links`` into already-ingested AIID incidents.

Incidents ingested before the table-format change carry no ``report_links``
key in their payload, so their rows render without source links (links are
joined from our own data at render time, never asked of the model). This
re-fetches the current snapshot, rebuilds the id -> links index with the
normal connector, and writes only the ``report_links`` key back into each
AIID incident's payload — nothing else on the row is touched, and the
Source cursor is left alone so the next weekly ingest behaves normally.

The database lives in the ``digest_data`` volume, so run this inside the app
container on the host that owns it:

    docker compose cp scripts/backfill_report_links.py app:/tmp/backfill_report_links.py
    docker compose exec app python /tmp/backfill_report_links.py
"""

from __future__ import annotations

import json

from sqlmodel import Session, select

from app.db import engine
from app.ingest.aiid import AIIDSnapshotConnector
from app.models import Incident


def main() -> None:
    connector = AIIDSnapshotConnector()
    result = connector.fetch(cursor="")  # empty cursor forces a full download
    links_by_id = {
        item.external_id: item.payload.get("report_links", [])
        for item in result.items
    }
    print(f"{result.note}; {len(links_by_id)} incidents in snapshot")

    updated = unchanged = missing = 0
    with Session(engine) as session:
        incidents = session.exec(
            select(Incident).where(Incident.source == "aiid")
        ).all()
        for inc in incidents:
            links = links_by_id.get(inc.external_id)
            if links is None:
                missing += 1
                continue
            payload = json.loads(inc.raw_payload or "{}")
            if payload.get("report_links") == links:
                unchanged += 1
                continue
            payload["report_links"] = links
            inc.raw_payload = json.dumps(payload, default=str)
            session.add(inc)
            updated += 1
        session.commit()
    print(f"updated {updated}, unchanged {unchanged}, not in snapshot {missing}")


if __name__ == "__main__":
    main()
