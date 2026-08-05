"""Tests for the daily AIID check: baseline, screening, digest delivery."""

import re

from sqlmodel import Session, select

from app.config import Settings
from app.db import engine, init_db
from app.llm import LLMError, Usage
from app.mail.aiid_check import BASELINE_SETTING, run_aiid_check
from app.models import (
    Incident,
    LLMUsage,
    OutboundEmail,
    Run,
    Setting,
    Source,
    utcnow,
)
from app.settings_store import get_setting, set_setting


class CheckLLM:
    """Fake LLM: screening flags the external_ids in `flag`; research and
    compose return minimal valid payloads (compose can be told to fail)."""

    def __init__(self, flag=(), fail_compose_for=()):
        self.flag = set(flag)
        self.fail_compose_for = set(fail_compose_for)
        self.screen_calls = 0
        self.compose_calls = 0

    def complete_json(self, *, system, user, schema, model, effort="high",
                      max_tokens=16000):
        if "headline" in schema.get("properties", {}):
            self.compose_calls += 1
            title = re.search(r"REPORTED TITLE: (.*)", user).group(1)
            if any(f in user for f in self.fail_compose_for):
                raise LLMError("compose failed", Usage(input_tokens=5))
            return (
                {
                    "headline": f"Digest: {title}",
                    "what_happened": "w", "mechanism_failed": "m",
                    "regime_applies": "r", "standard_of_care": "s",
                    "corroboration": "corroborated",
                    "corroboration_note": "ok",
                    "sources": [{"title": "src", "url": "https://s.example"}],
                },
                Usage(input_tokens=40, output_tokens=30),
            )
        # screening pass
        self.screen_calls += 1
        ids = re.findall(r'"external_id": "([^"]+)"', user)
        assessments = [
            {
                "incident_external_id": i,
                "has_privacy_angle": i in self.flag,
                "angle": "an angle" if i in self.flag else "",
                "salience": "high" if i in self.flag else "none",
            }
            for i in ids
        ]
        return {"assessments": assessments}, Usage(input_tokens=30, output_tokens=10)

    def complete_text_with_search(self, *, system, user, model, effort="high",
                                  max_tokens=6000, max_searches=5):
        return (
            "CONFIRMED FACTS\n- confirmed — https://s.example",
            Usage(input_tokens=80, output_tokens=40, web_searches=1),
        )


def _incident(external_id: str, *, screened=False, privacy=False) -> Incident:
    return Incident(
        source="aiid",
        external_id=external_id,
        dedup_key=f"aiid:{external_id}",
        title=f"Incident {external_id}",
        description="Something happened.",
        url=f"https://incidentdatabase.ai/cite/{external_id}",
        incident_date="2026-08-01",
        status="classified",
        llm_screened=screened,
    )


def _settings(**overrides) -> Settings:
    defaults = dict(digest_to="editor@example.org")
    defaults.update(overrides)
    return Settings(**defaults)


def _clean(session: Session) -> None:
    # Source rows too: with none enabled, run_ingest inside the check is a
    # no-op instead of downloading a real AIID snapshot mid-test.
    for model in (Incident, OutboundEmail, LLMUsage, Run, Setting, Source):
        for row in session.exec(select(model)).all():
            session.delete(row)
    session.commit()


def _sent_collector():
    sent = []
    return sent, (lambda settings, msg: sent.append(msg))


def test_first_run_establishes_baseline_without_email():
    init_db()
    with Session(engine) as s:
        _clean(s)
        s.add(_incident("100"))
        s.add(_incident("101"))
        s.commit()
        sent, deliver = _sent_collector()

        detail = run_aiid_check(s, _settings(), llm=CheckLLM(), deliver_fn=deliver)

        assert "baseline established — 2" in detail
        assert get_setting(s, BASELINE_SETTING)
        assert not sent
        assert all(
            i.notified_at is not None for i in s.exec(select(Incident)).all()
        )


def test_new_privacy_incident_goes_out_in_one_digest():
    init_db()
    with Session(engine) as s:
        _clean(s)
        set_setting(s, BASELINE_SETTING, utcnow().isoformat())
        s.add(_incident("200"))          # will be flagged
        s.add(_incident("201"))          # no angle -> suppressed
        s.commit()
        llm = CheckLLM(flag={"200"})
        sent, deliver = _sent_collector()

        detail = run_aiid_check(s, _settings(), llm=llm, deliver_fn=deliver)

        assert "digest sent" in detail and "1 write-ups" in detail
        assert len(sent) == 1
        assert "1 new privacy incident" in sent[0]["Subject"]

        out = s.exec(select(OutboundEmail)).one()
        assert out.kind == "digest" and out.status == "sent"

        by_id = {i.external_id: i for i in s.exec(select(Incident)).all()}
        assert by_id["200"].notified_at is not None
        assert by_id["200"].writeup_json != "{}"
        assert by_id["201"].notified_at is not None   # decided, never emailed
        assert by_id["201"].writeup_json == "{}"

        purposes = {u.purpose for u in s.exec(select(LLMUsage)).all()}
        assert purposes == {"screen", "aiid_research", "aiid_writeup"}

        # Next run: nothing left to do, no second email.
        detail2 = run_aiid_check(s, _settings(), llm=llm, deliver_fn=deliver)
        assert "no new incidents" in detail2 and len(sent) == 1


def test_failed_writeup_rolls_over_to_next_run():
    init_db()
    with Session(engine) as s:
        _clean(s)
        set_setting(s, BASELINE_SETTING, utcnow().isoformat())
        s.add(_incident("300"))
        s.add(_incident("301"))
        s.commit()
        llm = CheckLLM(flag={"300", "301"}, fail_compose_for={"Incident 300"})
        sent, deliver = _sent_collector()

        detail = run_aiid_check(s, _settings(), llm=llm, deliver_fn=deliver)

        assert "1 write-ups failed" in detail
        assert len(sent) == 1                          # digest still goes out
        by_id = {i.external_id: i for i in s.exec(select(Incident)).all()}
        assert by_id["301"].notified_at is not None
        assert by_id["300"].notified_at is None        # retried tomorrow


def test_digest_cap_defers_extras():
    init_db()
    with Session(engine) as s:
        _clean(s)
        set_setting(s, BASELINE_SETTING, utcnow().isoformat())
        for i in range(3):
            s.add(_incident(f"40{i}"))
        s.commit()
        llm = CheckLLM(flag={"400", "401", "402"})
        sent, deliver = _sent_collector()

        detail = run_aiid_check(
            s, _settings(aiid_digest_max=2), llm=llm, deliver_fn=deliver
        )
        assert "1 deferred" in detail
        remaining = [
            i for i in s.exec(select(Incident)).all() if i.notified_at is None
        ]
        assert len(remaining) == 1


def test_no_recipient_leaves_candidates_waiting():
    init_db()
    with Session(engine) as s:
        _clean(s)
        set_setting(s, BASELINE_SETTING, utcnow().isoformat())
        s.add(_incident("500"))
        s.commit()
        llm = CheckLLM(flag={"500"})
        sent, deliver = _sent_collector()

        detail = run_aiid_check(s, _settings(digest_to=""), llm=llm,
                                deliver_fn=deliver)

        assert "recipient not configured" in detail
        assert not sent and llm.screen_calls == 0
        assert s.exec(select(Incident)).one().notified_at is None
