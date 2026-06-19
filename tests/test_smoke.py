"""Smoke tests for the scaffold and auth flow."""

import os
import tempfile

os.environ.setdefault("APN_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("APN_SCHEDULER_ENABLED", "false")
os.environ.setdefault("APN_ADMIN_USERNAME", "admin")
os.environ.setdefault("APN_ADMIN_PASSWORD", "supersecret-pw-123")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_healthz():
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_root_redirects_when_anonymous():
    with TestClient(app) as client:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


def test_login_and_dashboard():
    with TestClient(app) as client:
        # Fetch login page to obtain a CSRF token in the session.
        page = client.get("/login")
        assert page.status_code == 200
        import re

        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

        resp = client.post(
            "/login",
            data={"username": "admin", "password": "supersecret-pw-123", "csrf_token": token},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        dash = client.get("/")
        assert dash.status_code == 200
        assert "Dashboard" in dash.text


def test_bad_login_rejected():
    with TestClient(app) as client:
        page = client.get("/login")
        import re

        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "wrong", "csrf_token": token},
        )
        assert resp.status_code == 200
        assert "Invalid credentials" in resp.text
