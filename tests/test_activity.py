"""Tests for the Activity review page."""

import re

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.main import app
from app.models import EmailMessage, OutboundEmail, Run


def _login(client: TestClient) -> None:
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    client.post(
        "/login",
        data={
            "username": "admin",
            "password": "supersecret-pw-123",
            "csrf_token": token,
        },
    )


def test_activity_requires_login():
    with TestClient(app) as client:
        resp = client.get("/activity", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


def test_activity_lists_email_traffic():
    with TestClient(app) as client:  # lifespan runs init_db
        with Session(engine) as s:
            s.add(
                EmailMessage(
                    uid=1, from_addr="alice@example.org",
                    subject="Robot read my diary", status="processed",
                    detail="Verified: diary leak",
                )
            )
            s.add(
                OutboundEmail(
                    kind="incident", to_addr="editor@example.org",
                    subject="[AI Privacy Digest] Diary leak", status="sent",
                    body_excerpt="Diary leak…",
                )
            )
            s.add(Run(job="email_poll", status="ok", detail="1 new; 1 processed"))
            s.commit()

        _login(client)
        page = client.get("/activity")
        assert page.status_code == 200
        assert "Robot read my diary" in page.text
        assert "[AI Privacy Digest] Diary leak" in page.text
        assert "email_poll" in page.text
