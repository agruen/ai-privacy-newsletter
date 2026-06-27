"""Tests for the LLM privacy-screening pass and its wiring into synthesis."""

import re

from sqlmodel import Session, select

from app.config import get_settings
from app.db import engine, init_db
from app.llm import Usage
from app.models import Incident, NewsletterItem
from app.synth.compose import generate_newsletter, month_incidents
from app.synth.ranking import score_incident
from app.synth.screen import screen_incidents
from test_synth import _clean, _incident


class ScreenLLM:
    """Fake LLM. Screening flags only external_ids in `flag`; synthesis and
    member-confirm return minimal valid payloads."""

    def __init__(self, flag, salience="high"):
        self.flag = set(flag)
        self.salience = salience
        self.calls = []

    def complete_json(self, *, system, user, schema, model, effort="high", max_tokens=16000):
        self.calls.append(model)
        if "screening AI incidents" in user:
            ids = re.findall(r'"external_id": "([^"]+)"', user)
            out = []
            for i in ids:
                hit = i in self.flag
                out.append({
                    "incident_external_id": i,
                    "has_privacy_angle": hit,
                    "angle": "a privacy angle" if hit else "",
                    "salience": self.salience if hit else "none",
                })
            return ({"assessments": out}, Usage(input_tokens=100, output_tokens=50))
        if "FLAGGED MEMBERS" in user or "member-watch" in user:
            return ({"results": []}, Usage(input_tokens=10, output_tokens=5))
        return ({"editor_note": "n", "featured": [], "brief_mentions": [],
                 "recommended_reading": [], "forward_look": "f"},
                Usage(input_tokens=50, output_tokens=20))


def test_screen_persists_and_caches():
    init_db()
    settings = get_settings()
    with Session(engine) as s:
        _clean(s)
        s.add(_incident("1", "2026-05-01", ["pii_leakage"], privacy=False))
        s.add(_incident("2", "2026-05-02", [], privacy=False))
        s.commit()

        llm = ScreenLLM(flag={"1"})
        assert screen_incidents(s, month_incidents(s, "2026-05"), llm, settings) == 2

        by = {i.external_id: i for i in month_incidents(s, "2026-05")}
        assert by["1"].llm_privacy_angle is True and by["1"].llm_salience == "high"
        assert by["2"].llm_privacy_angle is False and by["2"].llm_salience == ""
        assert all(i.llm_screened for i in by.values())

        # Cached: a second pass screens nothing (no re-bill).
        assert screen_incidents(s, month_incidents(s, "2026-05"), llm, settings) == 0
        # rescreen forces a re-run.
        assert screen_incidents(
            s, month_incidents(s, "2026-05"), llm, settings, rescreen=True
        ) == 2


def test_untagged_incident_included_when_llm_flags_it():
    init_db()
    settings = get_settings()
    with Session(engine) as s:
        _clean(s)
        # Neither is AIID-tagged privacy; the LLM flags only #1.
        s.add(_incident("1", "2026-07-01", [], privacy=False, title="Untagged privacy"))
        s.add(_incident("2", "2026-07-02", [], privacy=False, title="Not privacy"))
        s.commit()

        nl = generate_newsletter(s, "2026-07", ScreenLLM(flag={"1"}), settings)

        items = s.exec(
            select(NewsletterItem).where(NewsletterItem.newsletter_id == nl.id)
        ).all()
        inc_ids = {s.get(Incident, it.incident_id).external_id for it in items}
        assert "1" in inc_ids       # LLM-flagged, untagged → included
        assert "2" not in inc_ids   # no angle → excluded


def test_no_privacy_angle_raises():
    init_db()
    settings = get_settings()
    with Session(engine) as s:
        _clean(s)
        s.add(_incident("1", "2026-08-01", [], privacy=False))
        s.commit()
        try:
            generate_newsletter(s, "2026-08", ScreenLLM(flag=set()), settings)
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "privacy-angle" in str(exc)


def test_salience_boosts_ranking():
    high = _incident("1", "2026-05-01", [], privacy=False)
    high.llm_salience = "high"
    low = _incident("2", "2026-05-01", [], privacy=False)
    low.llm_salience = "low"
    assert score_incident(high) > score_incident(low)
