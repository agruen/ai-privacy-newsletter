"""Phase 6 review-UI tests: view, edit, approve, export."""

import json
import re

from sqlmodel import Session, select

from app.db import engine, init_db
from app.models import Newsletter


SAMPLE = {
    "rows": [{
        "incident_external_id": "1", "headline": "Original headline",
        "what_happened": "w",
        "risk_category": "Vendor Data Exposure",
        "risk_explanation": "No processor controls.",
    }],
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
        "row-0-incident_external_id": "1",
        "row-0-headline": "Edited headline",
        "row-0-what_happened": "w",
        "row-0-risk_category": "Vendor Data Exposure",
        "row-0-risk_explanation": "No processor controls.",
    }
    resp = client.post(f"/newsletters/{nid}/edit", data=form, follow_redirects=True)
    assert resp.status_code == 200
    with Session(engine) as session:
        content = json.loads(session.get(Newsletter, nid).content_json)
        assert content["rows"][0]["headline"] == "Edited headline"
        assert content["rows"][0]["risk_category"] == "Vendor Data Exposure"

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
        data={**form, "row-0-headline": "should not save"},
        follow_redirects=True,
    )
    assert blocked.status_code == 200
    with Session(engine) as session:
        saved = json.loads(session.get(Newsletter, nid).content_json)
        assert saved["rows"][0]["headline"] == "Edited headline"


def test_row_removal_drops_row():
    nid = _seed_newsletter()
    client, token = _client_and_token()
    form = {
        "csrf_token": token,
        "row-0-incident_external_id": "1", "row-0-headline": "Original headline",
        "row-0-what_happened": "w", "row-0-risk_category": "C",
        "row-0-risk_explanation": "e",
        "row-0-remove": "on",
    }
    client.post(f"/newsletters/{nid}/edit", data=form, follow_redirects=True)
    with Session(engine) as session:
        assert json.loads(session.get(Newsletter, nid).content_json)["rows"] == []


def test_removing_a_row_keeps_the_ones_after_it():
    """The form indexes rows positionally; a removal must not truncate the rest."""
    nid = _seed_newsletter()
    client, token = _client_and_token()
    form = {
        "csrf_token": token,
        "row-0-incident_external_id": "1", "row-0-headline": "First",
        "row-0-what_happened": "w", "row-0-risk_category": "C",
        "row-0-risk_explanation": "e", "row-0-remove": "on",
        "row-1-incident_external_id": "2", "row-1-headline": "Second",
        "row-1-what_happened": "w2", "row-1-risk_category": "C2",
        "row-1-risk_explanation": "e2",
    }
    client.post(f"/newsletters/{nid}/edit", data=form, follow_redirects=True)
    with Session(engine) as session:
        rows = json.loads(session.get(Newsletter, nid).content_json)["rows"]
    assert [r["headline"] for r in rows] == ["Second"]
