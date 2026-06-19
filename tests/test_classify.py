"""Phase 3 classification tests."""

import json

from app.classify.aiid_tags import AIIDTagClassifier
from app.classify.base import (
    STATUS_CLASSIFIED,
    STATUS_NOT_PRIVACY,
    STATUS_PENDING,
)
from app.classify.runner import classify_pending
from app.db import engine, init_db
from app.models import Incident
from sqlmodel import Session, select


def _payload(mit: dict | None) -> dict:
    return {"classifications": {"MIT": mit} if mit else {}}


def test_privacy_subdomain_classified():
    c = AIIDTagClassifier()
    res = c.classify(_payload({"Risk Domain": "2. Privacy & Security",
                               "Risk Subdomain": "2.1. Compromise of privacy ..."}))
    assert res.status == STATUS_CLASSIFIED
    assert res.is_privacy is True
    assert "pii_leakage" in res.categories
    assert res.confidence == 1.0


def test_security_subdomain_maps_to_security_vuln():
    c = AIIDTagClassifier()
    res = c.classify(_payload({"Risk Domain": "2. Privacy & Security",
                               "Risk Subdomain": "2.2. AI system security ..."}))
    assert res.is_privacy is True
    assert res.categories == ["security_vuln"]


def test_non_privacy_domain():
    c = AIIDTagClassifier()
    res = c.classify(_payload({"Risk Domain": "4. Malicious Actors & Misuse",
                               "Risk Subdomain": "4.1 ..."}))
    assert res.status == STATUS_NOT_PRIVACY
    assert res.is_privacy is False


def test_missing_mit_stays_pending():
    c = AIIDTagClassifier()
    res = c.classify(_payload(None))
    assert res.status == STATUS_PENDING


def test_classify_pending_updates_rows():
    init_db()
    with Session(engine) as session:
        # Clean slate for this test's incidents.
        for r in session.exec(select(Incident)).all():
            session.delete(r)
        session.commit()

        session.add(Incident(
            source="t", external_id="p1", dedup_key="t:p1", title="leak",
            raw_payload=json.dumps(_payload({"Risk Domain": "2. Privacy & Security",
                                             "Risk Subdomain": "2.1. x"})),
        ))
        session.add(Incident(
            source="t", external_id="n1", dedup_key="t:n1", title="other",
            raw_payload=json.dumps(_payload({"Risk Domain": "3. Misinformation",
                                             "Risk Subdomain": "3.1"})),
        ))
        session.add(Incident(
            source="t", external_id="u1", dedup_key="t:u1", title="unclassified",
            raw_payload=json.dumps(_payload(None)),
        ))
        session.commit()

        decided = classify_pending(session)
        assert decided == 2  # privacy + not_privacy decided; unclassified stays pending

        rows = {r.external_id: r for r in session.exec(select(Incident)).all()}
        assert rows["p1"].is_privacy is True
        assert rows["n1"].status == STATUS_NOT_PRIVACY
        assert rows["u1"].status == STATUS_PENDING
