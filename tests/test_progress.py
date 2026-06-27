"""Tests for ingest progress tracking and the /sources/status endpoint."""

import os
import re
import tempfile

from sqlmodel import Session, select

from app.db import engine, init_db
from app.ingest import runner
from app.ingest.aiid import AIIDSnapshotConnector
from app.models import Incident, Source
from app.progress import IngestProgress
from test_ingest import build_fixture


class Recorder:
    """Minimal stand-in for IngestProgress that records every update."""

    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **fields) -> None:
        self.updates.append(fields)


def test_progress_lifecycle():
    p = IngestProgress()
    assert p.snapshot()["active"] is False
    assert p.snapshot()["status"] == ""

    p.start("go")
    assert p.is_active()
    assert p.snapshot()["status"] == "running"

    p.update(phase="downloading", downloaded=10, download_total=100)
    assert p.snapshot()["downloaded"] == 10

    p.finish("ok", "done")
    assert not p.is_active()
    snap = p.snapshot()
    assert snap["status"] == "ok" and snap["phase"] == "done"

    # Updates after a run has finished are ignored.
    p.update(downloaded=999)
    assert p.snapshot()["downloaded"] == 10


def test_connector_reports_parse_progress():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = os.path.join(tmp, "backup-20260101000000.tar.bz2")
        build_fixture(fixture)
        rec = Recorder()
        result = AIIDSnapshotConnector(snapshot_file=fixture).fetch("", rec)

    assert len(result.items) == 2
    phases = [u["phase"] for u in rec.updates if "phase" in u]
    assert "parsing" in phases
    parsed = [u["parsed"] for u in rec.updates if "parsed" in u]
    assert parsed and parsed[-1] == 2


def test_runner_reports_store_progress(monkeypatch):
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

            rec = Recorder()
            summaries = runner.run_ingest(session, rec)

    assert summaries[0].created == 2
    phases = [u["phase"] for u in rec.updates if "phase" in u]
    assert "storing" in phases
    created = [u["created"] for u in rec.updates if "created" in u]
    assert created and created[-1] == 2


def test_status_endpoint_requires_auth_and_returns_json():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    # Unauthenticated → redirect to login, not a JSON status.
    r = client.get("/sources/status", follow_redirects=False)
    assert r.status_code in (302, 303, 307)

    # Authenticated → JSON snapshot of the tracker.
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    client.post(
        "/login",
        data={"username": "admin", "password": "supersecret-pw-123", "csrf_token": token},
    )
    r = client.get("/sources/status")
    assert r.status_code == 200
    body = r.json()
    assert "active" in body and "phase" in body and "status" in body
