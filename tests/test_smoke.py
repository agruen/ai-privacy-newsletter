"""Smoke tests for the Phase 0 scaffold."""

import os
import tempfile

os.environ.setdefault("APN_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("APN_SCHEDULER_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_healthz():
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_index():
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "AI Privacy Incident Digest" in resp.text
