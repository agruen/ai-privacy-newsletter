"""Phase 2 ingestion tests using a synthetic AIID snapshot fixture."""

import io
import os
import tarfile
import tempfile

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.ingest import runner  # noqa: E402
from app.ingest.aiid import AIIDSnapshotConnector  # noqa: E402
from app.models import Incident, Source  # noqa: E402

INCIDENTS_CSV = (
    "_id,incident_id,date,reports,Alleged deployer of AI system,"
    "Alleged developer of AI system,Alleged harmed or nearly harmed parties,"
    "description,title\n"
    "a1,901,2026-05-01,1;2,DeployCo,DevCo,Users,A privacy leak happened,Model leaked PII\n"
    "a2,902,2026-05-02,3,OtherCo,OtherDev,Public,A bus crashed,Self-driving mishap\n"
)

MIT_CSV = (
    "Namespace,Incident ID,Published,Risk Domain,Risk Subdomain,Entity,Timing,Intent\n"
    "MIT,901,true,2. Privacy & Security,"
    "2.1. Compromise of privacy by obtaining; leaking or inferring information,"
    "AI,Post-deployment,Unintentional\n"
    "MIT,902,true,7. AI system safety; failures; and limitations,"
    "7.3. Lack of capability or robustness,AI,Post-deployment,Unintentional\n"
)


def build_fixture(path: str) -> None:
    """Write a minimal mongodump-style tar.bz2 snapshot."""
    with tarfile.open(path, "w:bz2") as tar:
        for name, content in (
            ("mongodump_full_snapshot/incidents.csv", INCIDENTS_CSV),
            ("mongodump_full_snapshot/classifications_MIT.csv", MIT_CSV),
        ):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_connector_parses_incidents_and_mit():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = os.path.join(tmp, "backup-20260101000000.tar.bz2")
        build_fixture(fixture)
        result = AIIDSnapshotConnector(snapshot_file=fixture).fetch("")

    by_id = {i.external_id: i for i in result.items}
    assert set(by_id) == {"901", "902"}
    assert by_id["901"].title == "Model leaked PII"
    assert by_id["901"].url == "https://incidentdatabase.ai/cite/901"
    # MIT classification is attached for the classifier to use later.
    assert by_id["901"].payload["classifications"]["MIT"]["Risk Domain"].startswith("2.")
    assert "MIT" in by_id["902"].payload["classifications"]


def test_run_ingest_upserts(monkeypatch):
    init_db()
    with tempfile.TemporaryDirectory() as tmp:
        fixture = os.path.join(tmp, "backup-20260101000000.tar.bz2")
        build_fixture(fixture)

        monkeypatch.setitem(
            runner.CONNECTORS,
            "aiid_snapshot",
            lambda name: AIIDSnapshotConnector(source_name=name, snapshot_file=fixture),
        )

        with Session(engine) as session:
            for r in session.exec(select(Incident)).all():
                session.delete(r)
            session.commit()
            src = session.get(Source, "aiid") or Source(name="aiid", kind="aiid_snapshot")
            session.add(src)
            session.commit()

            first = runner.run_ingest(session)
            assert first[0].created == 2

            # Re-run is idempotent: no new rows, both updated.
            second = runner.run_ingest(session)
            assert second[0].created == 0
            assert second[0].updated == 2

            rows = session.exec(select(Incident)).all()
            assert len(rows) == 2
            assert all(r.status == "pending" for r in rows)
