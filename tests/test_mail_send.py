"""Tests for outbound rendering and send bookkeeping."""

import json

from sqlmodel import Session, select

from app.config import Settings
from app.db import engine, init_db
from app.mail.send import (
    compose_digest_email,
    compose_incident_email,
    send_and_record,
)
from app.models import Incident, OutboundEmail

WRITEUP = {
    "headline": "Model leaked PII",
    "what_happened": "Data <escaped> here.",
    "mechanism_failed": "Leakage.",
    "regime_applies": "FTC Act.",
    "standard_of_care": "Filtering.",
    "corroboration": "partially_corroborated",
    "corroboration_note": "one source only",
    "sources": [{"title": "Story", "url": "https://n.example/1"}],
}


def _email_incident() -> Incident:
    return Incident(
        source="email", external_id="em-1", dedup_key="email:em-1",
        title="t", incident_date="2026-08-05",
        raw_payload=json.dumps({"from": "alice@example.org"}),
    )


def _aiid_incident() -> Incident:
    return Incident(
        source="aiid", external_id="777", dedup_key="aiid:777", title="t",
        url="https://incidentdatabase.ai/cite/777",
    )


def test_incident_email_carries_writeup_and_provenance():
    subject, text, html = compose_incident_email(_email_incident(), WRITEUP)
    assert subject == "[AI Privacy Digest] Model leaked PII"
    assert "What happened: Data <escaped> here." in text
    assert "Corroboration: partially_corroborated — one source only" in text
    assert "https://n.example/1" in text
    assert "submitted by email from alice@example.org on 2026-08-05" in text
    assert "&lt;escaped&gt;" in html          # HTML body is escaped


def test_digest_email_bundles_multiple_writeups():
    entries = [(_aiid_incident(), WRITEUP), (_aiid_incident(), WRITEUP)]
    subject, text, html = compose_digest_email(entries)
    assert "2 new privacy incidents" in subject
    assert text.count("Model leaked PII") == 2
    assert "AI Incident Database #777" in text


def test_send_and_record_success_and_failure():
    init_db()
    with Session(engine) as s:
        for row in s.exec(select(OutboundEmail)).all():
            s.delete(row)
        s.commit()
        settings = Settings()
        sent = []

        send_and_record(
            s, settings, kind="incident", to_addr="e@x.org", subject="s",
            text="body", html_body="<p>body</p>", incident_ids=[1],
            deliver_fn=lambda st, msg: sent.append(msg),
        )
        ok = s.exec(select(OutboundEmail)).one()
        assert ok.status == "sent" and json.loads(ok.incident_ids) == [1]
        assert sent[0]["Auto-Submitted"] == "auto-generated"

        def boom(st, msg):
            raise RuntimeError("SMTP down")

        try:
            send_and_record(
                s, settings, kind="digest", to_addr="e@x.org", subject="s2",
                text="b", html_body="<p>b</p>", incident_ids=[2],
                deliver_fn=boom,
            )
            assert False, "expected the delivery error to propagate"
        except RuntimeError:
            pass
        rows = s.exec(select(OutboundEmail).order_by(OutboundEmail.id)).all()
        assert rows[-1].status == "error" and "SMTP down" in rows[-1].detail
