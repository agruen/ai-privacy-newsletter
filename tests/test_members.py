"""Phase 4 member matching + CRUD tests."""

import json
import re

from app.matching import find_member_mentions, member_terms
from app.models import Member


def test_member_terms_dedup_and_min_length():
    m = Member(name="Acme", aliases=json.dumps(["Acme", "Acme AI", "X"]))
    terms = member_terms(m)
    assert "Acme" in terms and "Acme AI" in terms
    assert "X" not in terms          # too short
    assert terms.count("Acme") == 1  # de-duped


def test_find_mentions_word_boundary():
    members = [
        Member(id=1, name="Acme Corp", aliases=json.dumps(["Acme AI"])),
        Member(id=2, name="Globex", aliases="[]"),
    ]
    text = "Last month Acme AI disclosed a breach. Unrelated: Globextra is not Globex."
    matches = find_member_mentions(text, members)
    by_name = {m.member.name: m for m in matches}
    assert "Acme Corp" in by_name        # matched via alias
    assert "Globex" in by_name           # matched whole word, not 'Globextra'
    assert "Acme AI" in by_name["Acme Corp"].snippet


def test_no_false_substring_match():
    members = [Member(id=1, name="Meta", aliases="[]")]
    assert find_member_mentions("metadata and metabolism", members) == []
    assert len(find_member_mentions("Meta released a model", members)) == 1


def _login(client):
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    client.post("/login", data={"username": "admin", "password": "supersecret-pw-123",
                                "csrf_token": token})
    return token


def test_member_crud_and_csv_import():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        token = _login(client)
        client.post("/members/add", data={"name": "Test Co", "aliases": "TestCo; TC Inc",
                                           "notes": "n", "csrf_token": token})
        page = client.get("/members")
        assert "Test Co" in page.text
        assert "TestCo" in page.text

        csv_data = "name,aliases,notes\nImported Inc,Imp;Imported\nTest Co,TestCo Updated\n"
        resp = client.post(
            "/members/import",
            data={"csrf_token": token},
            files={"file": ("members.csv", csv_data, "text/csv")},
            follow_redirects=True,
        )
        assert "Imported" in resp.text
        assert resp.url.path == "/members"
