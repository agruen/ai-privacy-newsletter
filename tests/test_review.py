"""Phase 6 review-UI tests: view, edit, approve, export."""

import json
import re

from sqlmodel import Session, select

from app.db import engine, init_db
from app.models import Newsletter


SAMPLE = {
    "editor_note": "Original note.",
    "featured": [{
        "incident_external_id": "1", "headline": "Original headline",
        "what_happened": "w", "mechanism_failed": "m",
        "regime_applies": "GDPR", "standard_of_care": "s",
    }],
    "brief_mentions": [{"incident_external_id": "2", "summary": "brief one"}],
    "recommended_reading": [{"title": "Doc", "url": "https://d", "note": "n"}],
    "forward_look": "Watch this.",
}


def _seed_newsletter() -> int:
    init_db()
    with Session(engine) as session:
        nl = Newsletter(period="2026-05", status="draft", content_json=json.dumps(SAMPLE))
        session.add(nl)
        session.commit()
        session.refresh(nl)
        return nl.id


def _client_and_token():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    client.post("/login", data={"username": "admin", "password": "supersecret-pw-123",
                                "csrf_token": token})
    return client, token


def test_detail_edit_approve_export():
    nid = _seed_newsletter()
    client, token = _client_and_token()

    # View
    page = client.get(f"/newsletters/{nid}")
    assert page.status_code == 200
    assert "Original headline" in page.text

    # Edit: change editor note + headline; keep one of each other section.
    form = {
        "csrf_token": token,
        "editor_note": "Edited note.",
        "forward_look": "Edited forward.",
        "featured-0-incident_external_id": "1",
        "featured-0-headline": "Edited headline",
        "featured-0-what_happened": "w", "featured-0-mechanism_failed": "m",
        "featured-0-regime_applies": "GDPR", "featured-0-standard_of_care": "s",
        "brief-0-incident_external_id": "2", "brief-0-summary": "brief one",
        "reading-0-title": "Doc", "reading-0-url": "https://d", "reading-0-note": "n",
    }
    resp = client.post(f"/newsletters/{nid}/edit", data=form, follow_redirects=True)
    assert resp.status_code == 200
    with Session(engine) as session:
        content = json.loads(session.get(Newsletter, nid).content_json)
        assert content["editor_note"] == "Edited note."
        assert content["featured"][0]["headline"] == "Edited headline"

    # Export page renders all three formats.
    exp = client.get(f"/newsletters/{nid}/export")
    assert exp.status_code == 200
    assert "Edited headline" in exp.text
    assert "Markdown" in exp.text and "Plain text" in exp.text

    # Download markdown as an attachment.
    dl = client.get(f"/newsletters/{nid}/download?fmt=md")
    assert dl.status_code == 200
    assert "attachment" in dl.headers["content-disposition"]
    assert "Edited headline" in dl.text

    # Approve locks the issue; further edits are refused.
    client.post(f"/newsletters/{nid}/approve", data={"csrf_token": token})
    with Session(engine) as session:
        assert session.get(Newsletter, nid).status == "approved"
    blocked = client.post(
        f"/newsletters/{nid}/edit",
        data={**form, "editor_note": "should not save"},
        follow_redirects=True,
    )
    assert blocked.status_code == 200
    with Session(engine) as session:
        assert json.loads(session.get(Newsletter, nid).content_json)["editor_note"] == "Edited note."


def test_featured_removal_drops_story():
    nid = _seed_newsletter()
    client, token = _client_and_token()
    form = {
        "csrf_token": token, "editor_note": "n", "forward_look": "f",
        "featured-0-incident_external_id": "1", "featured-0-headline": "Original headline",
        "featured-0-what_happened": "w", "featured-0-mechanism_failed": "m",
        "featured-0-regime_applies": "r", "featured-0-standard_of_care": "s",
        "featured-0-remove": "on",
    }
    client.post(f"/newsletters/{nid}/edit", data=form, follow_redirects=True)
    with Session(engine) as session:
        assert json.loads(session.get(Newsletter, nid).content_json)["featured"] == []
