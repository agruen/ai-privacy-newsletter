"""Phase 5 synthesis tests (ranking, cost accounting, compose) with a fake LLM."""

import json

from sqlmodel import Session, select

from app.config import get_settings
from app.db import engine, init_db
from app.llm import Usage, cost_of, month_spend
from app.models import (
    Incident,
    LLMUsage,
    Member,
    MemberFlag,
    Newsletter,
    NewsletterItem,
    utcnow,
)
from app.synth.compose import generate_newsletter
from app.synth.ranking import rank, score_incident


def _incident(ext, date, cats, reports="", harmed="", title="t", desc="d", privacy=True):
    return Incident(
        source="t", external_id=ext, dedup_key=f"t:{ext}", title=title, description=desc,
        url=f"https://x/{ext}", incident_date=date, is_privacy=privacy, status="classified",
        categories=json.dumps(cats),
        raw_payload=json.dumps({"reports": reports, "harmed_parties": harmed}),
    )


def test_cost_of_opus():
    c = cost_of("claude-opus-4-8", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert round(c, 2) == 30.0  # $5 in + $25 out


def test_scoring_prioritizes_pii_and_corroboration():
    strong = _incident("1", "2026-05-02", ["pii_leakage"], reports="a;b;c", harmed="users")
    weak = _incident("2", "2026-05-01", ["security_vuln"])
    assert score_incident(strong) > score_incident(weak)
    ordered = rank([weak, strong])
    assert ordered[0].incident.external_id == "1"


class FakeLLM:
    """Returns canned content and a fixed token usage per call."""

    def __init__(self):
        self.calls = []

    def complete_json(self, *, system, user, schema, model, effort="high", max_tokens=16000):
        self.calls.append({"model": model, "effort": effort})
        if "member-watch" in user or "FLAGGED MEMBERS" in user:
            return ({"results": [{"member": "Acme AI", "is_about_member": True,
                                  "note": "subject of featured story"}]},
                    Usage(input_tokens=500, output_tokens=100))
        content = {
            "editor_note": "A busy month for AI privacy. Acme AI featured prominently.",
            "featured": [{
                "incident_external_id": "1", "headline": "Acme AI leaked PII",
                "what_happened": "x", "mechanism_failed": "y",
                "regime_applies": "GDPR", "standard_of_care": "z",
            }],
            "brief_mentions": [{"incident_external_id": "2", "summary": "minor issue"}],
            "recommended_reading": [{"title": "Report", "url": "https://r", "note": "n"}],
            "forward_look": "Watch for regulator action.",
        }
        return (content, Usage(input_tokens=3000, output_tokens=1500))


def _clean(session):
    for model in (MemberFlag, NewsletterItem, LLMUsage, Newsletter, Incident, Member):
        for row in session.exec(select(model)).all():
            session.delete(row)
    session.commit()


def test_generate_newsletter_end_to_end():
    init_db()
    settings = get_settings()
    with Session(engine) as session:
        _clean(session)
        session.add(_incident("1", "2026-05-02", ["pii_leakage"], reports="a;b", harmed="users",
                              title="Acme AI leaked PII"))
        session.add(_incident("2", "2026-05-10", ["security_vuln"], title="Minor flaw"))
        session.add(_incident("9", "2026-04-15", ["pii_leakage"], title="Old"))  # other month
        session.add(Member(name="Acme AI", aliases="[]"))
        session.commit()

        llm = FakeLLM()
        nl = generate_newsletter(session, "2026-05", llm, settings)

        assert nl.status == "draft"
        content = json.loads(nl.content_json)
        assert content["featured"][0]["headline"] == "Acme AI leaked PII"

        items = session.exec(
            select(NewsletterItem).where(NewsletterItem.newsletter_id == nl.id)
        ).all()
        # Two May incidents placed; April one excluded.
        assert len(items) == 2

        flags = session.exec(
            select(MemberFlag).where(MemberFlag.newsletter_id == nl.id)
        ).all()
        assert len(flags) == 1
        assert flags[0].member_name == "Acme AI"
        assert flags[0].confirmed is True

        # Two LLM calls recorded with positive cost.
        usage_rows = session.exec(select(LLMUsage)).all()
        assert len(usage_rows) == 2
        assert month_spend(session) > 0
        # Synthesis used the configured model; confirm used the cheap one.
        assert {c["model"] for c in llm.calls} == {
            settings.anthropic_model, settings.anthropic_confirm_model
        }


def test_no_incidents_raises():
    init_db()
    settings = get_settings()
    with Session(engine) as session:
        _clean(session)
        try:
            generate_newsletter(session, "2099-01", FakeLLM(), settings)
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_duplicate_refused_then_regenerated():
    from app.synth.compose import NewsletterExists

    init_db()
    settings = get_settings()
    with Session(engine) as session:
        _clean(session)
        session.add(_incident("1", "2026-05-02", ["pii_leakage"], reports="a;b",
                              harmed="users", title="Acme AI leaked PII"))
        session.add(Member(name="Acme AI", aliases="[]"))
        session.commit()

        nl1 = generate_newsletter(session, "2026-05", FakeLLM(), settings)

        # A second plain generate is refused — no duplicate, no extra spend.
        spend_before = month_spend(session)
        try:
            generate_newsletter(session, "2026-05", FakeLLM(), settings)
            assert False, "expected NewsletterExists"
        except NewsletterExists as exc:
            assert exc.newsletter_id == nl1.id
        assert month_spend(session) == spend_before
        only = session.exec(
            select(Newsletter).where(Newsletter.period == "2026-05")
        ).all()
        assert len(only) == 1

        # Regenerate replaces the prior draft: still exactly one, with a new id,
        # and its child rows belong to the new draft only.
        nl2 = generate_newsletter(session, "2026-05", FakeLLM(), settings, regenerate=True)
        after = session.exec(
            select(Newsletter).where(Newsletter.period == "2026-05")
        ).all()
        assert len(after) == 1 and after[0].id == nl2.id and nl2.id != nl1.id
        orphan_items = session.exec(
            select(NewsletterItem).where(NewsletterItem.newsletter_id == nl1.id)
        ).all()
        assert orphan_items == []
