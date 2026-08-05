"""Tests for the inbox poll: cursor discipline, triage, write-ups, retries."""

import json

from sqlmodel import Session, select

from app.config import Settings
from app.db import engine, init_db
from app.llm import Usage
from app.mail.poll import (
    LAST_UID_SETTING,
    UIDVALIDITY_SETTING,
    poll_inbox,
)
from app.models import (
    EmailMessage,
    Incident,
    LLMUsage,
    OutboundEmail,
    Run,
    Setting,
)
from app.settings_store import get_setting
from test_mail_parse import raw_email


class FakeInbox:
    """In-memory IMAP stand-in keyed by UID."""

    def __init__(self, messages: dict[int, bytes], uidval: int = 1):
        self.messages = messages
        self.uidval = uidval
        self.broken_uids: set[int] = set()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def uidvalidity(self) -> int:
        return self.uidval

    def uids_above(self, last_uid: int) -> list[int]:
        return sorted(u for u in self.messages if u > last_uid)

    def fetch(self, uid: int) -> bytes:
        if uid in self.broken_uids:
            raise RuntimeError("fetch blew up")
        return self.messages[uid]


class MailLLM:
    """Fake LLM for the email pipeline: classify + research + compose."""

    def __init__(self, incident: bool = True):
        self.incident = incident
        self.classify_calls = 0
        self.research_calls = 0
        self.compose_calls = 0

    def complete_json(self, *, system, user, schema, model, effort="high",
                      max_tokens=16000):
        if "headline" in schema.get("properties", {}):
            self.compose_calls += 1
            return (
                {
                    "headline": "Verified: Model leaked PII",
                    "what_happened": "A model exposed personal data.",
                    "mechanism_failed": "Training-data leakage.",
                    "regime_applies": "FTC Act; state privacy laws.",
                    "standard_of_care": "Filter training corpora.",
                    "corroboration": "corroborated",
                    "corroboration_note": "Two independent reports.",
                    "sources": [{"title": "Report", "url": "https://s.example/1"}],
                },
                Usage(input_tokens=100, output_tokens=80),
            )
        self.classify_calls += 1
        return (
            {
                "is_privacy_incident": self.incident,
                "confidence": 0.9,
                "title": "Model leaked PII",
                "summary": "A model exposed personal data of users.",
                "urls": ["https://news.example/story"],
                "angle": "PII leakage",
                "salience": "high" if self.incident else "none",
                "reason": "reports a privacy incident" if self.incident
                else "newsletter chatter",
            },
            Usage(input_tokens=50, output_tokens=20),
        )

    def complete_text_with_search(self, *, system, user, model, effort="high",
                                  max_tokens=6000, max_searches=5):
        self.research_calls += 1
        return (
            "CONFIRMED FACTS\n- The leak happened — https://s.example/1\n"
            "UNVERIFIED CLAIMS\n- none\nSOURCES\n- https://s.example/1",
            Usage(input_tokens=200, output_tokens=100, web_searches=2),
        )


def _settings(**overrides) -> Settings:
    defaults = dict(digest_to="editor@example.org", imap_folder="INBOX")
    defaults.update(overrides)
    return Settings(**defaults)


def _clean(session: Session) -> None:
    for model in (EmailMessage, OutboundEmail, LLMUsage, Run, Incident, Setting):
        for row in session.exec(select(model)).all():
            session.delete(row)
    session.commit()


def _sent_collector():
    sent = []
    return sent, (lambda settings, msg: sent.append(msg))


def test_incident_email_end_to_end():
    init_db()
    with Session(engine) as s:
        _clean(s)
        llm = MailLLM()
        sent, deliver = _sent_collector()
        inbox = FakeInbox({5: raw_email()})

        summary = poll_inbox(
            s, _settings(), inbox_factory=lambda: inbox, llm=llm, deliver_fn=deliver
        )

        assert summary.processed == 1 and summary.errors == 0
        assert llm.classify_calls == llm.research_calls == llm.compose_calls == 1

        incident = s.exec(select(Incident)).one()
        assert incident.source == "email"
        assert incident.is_privacy and incident.llm_privacy_angle
        assert json.loads(incident.writeup_json)["headline"].startswith("Verified")
        assert incident.notified_at is not None

        row = s.exec(select(EmailMessage)).one()
        assert row.status == "processed" and row.incident_id == incident.id

        out = s.exec(select(OutboundEmail)).one()
        assert out.kind == "incident" and out.status == "sent"
        assert out.to_addr == "editor@example.org"
        assert len(sent) == 1 and "Verified" in sent[0]["Subject"]

        # LLM spend was recorded for all three steps (incl. web searches).
        purposes = {u.purpose for u in s.exec(select(LLMUsage)).all()}
        assert purposes == {"email_classify", "email_research", "email_writeup"}

        assert get_setting(s, LAST_UID_SETTING) == "5"
        assert get_setting(s, UIDVALIDITY_SETTING) == "1"

        # Second poll: nothing new, no extra LLM calls, no extra sends.
        again = poll_inbox(
            s, _settings(), inbox_factory=lambda: inbox, llm=llm, deliver_fn=deliver
        )
        assert again.new == 0 and llm.classify_calls == 1 and len(sent) == 1


def test_automated_mail_is_skipped_without_llm():
    init_db()
    with Session(engine) as s:
        _clean(s)
        llm = MailLLM()
        sent, deliver = _sent_collector()
        inbox = FakeInbox(
            {
                1: raw_email(headers={"Auto-Submitted": "auto-generated"},
                             message_id="<a@x>"),
                2: raw_email(from_addr="mailer-daemon@mx.example.org",
                             message_id="<b@x>"),
            }
        )
        summary = poll_inbox(
            s, _settings(), inbox_factory=lambda: inbox, llm=llm, deliver_fn=deliver
        )
        assert summary.skipped == 2 and summary.processed == 0
        assert llm.classify_calls == 0 and not sent
        statuses = {m.status for m in s.exec(select(EmailMessage)).all()}
        assert statuses == {"skipped"}
        assert get_setting(s, LAST_UID_SETTING) == "2"


def test_not_an_incident_is_logged_and_not_sent():
    init_db()
    with Session(engine) as s:
        _clean(s)
        llm = MailLLM(incident=False)
        sent, deliver = _sent_collector()
        summary = poll_inbox(
            s, _settings(),
            inbox_factory=lambda: FakeInbox({3: raw_email()}),
            llm=llm, deliver_fn=deliver,
        )
        assert summary.not_incident == 1
        assert llm.research_calls == 0 and not sent
        assert s.exec(select(Incident)).first() is None
        row = s.exec(select(EmailMessage)).one()
        assert row.status == "not_incident" and "newsletter" in row.detail


def test_budget_stop_spends_nothing_and_keeps_message():
    init_db()
    with Session(engine) as s:
        _clean(s)
        llm = MailLLM()
        sent, deliver = _sent_collector()
        summary = poll_inbox(
            s, _settings(anthropic_monthly_budget_usd=0),
            inbox_factory=lambda: FakeInbox({4: raw_email()}),
            llm=llm, deliver_fn=deliver,
        )
        assert "budget" in summary.stopped
        assert llm.classify_calls == 0 and not sent
        # Cursor did not advance: the message is retried when budget frees up.
        assert get_setting(s, LAST_UID_SETTING, "0") == "0"


def test_duplicate_message_id_processed_once():
    init_db()
    with Session(engine) as s:
        _clean(s)
        llm = MailLLM()
        sent, deliver = _sent_collector()
        same = raw_email(message_id="<dup@example.org>")
        summary = poll_inbox(
            s, _settings(),
            inbox_factory=lambda: FakeInbox({1: same, 2: same}),
            llm=llm, deliver_fn=deliver,
        )
        assert summary.processed == 1 and summary.skipped == 1
        assert len(s.exec(select(Incident)).all()) == 1
        assert len(sent) == 1


def test_send_failure_retries_without_rebilling():
    init_db()
    with Session(engine) as s:
        _clean(s)
        llm = MailLLM()

        def broken_deliver(settings, msg):
            raise RuntimeError("SMTP down")

        inbox = FakeInbox({9: raw_email()})
        first = poll_inbox(
            s, _settings(), inbox_factory=lambda: inbox,
            llm=llm, deliver_fn=broken_deliver,
        )
        assert first.errors == 1
        incident = s.exec(select(Incident)).one()
        assert incident.writeup_json != "{}"       # write-up cached
        assert incident.notified_at is None        # but not delivered
        assert get_setting(s, LAST_UID_SETTING, "0") == "0"  # will retry
        failed = s.exec(select(OutboundEmail)).one()
        assert failed.status == "error"

        sent, deliver = _sent_collector()
        second = poll_inbox(
            s, _settings(), inbox_factory=lambda: inbox,
            llm=llm, deliver_fn=deliver,
        )
        assert second.processed == 1 and len(sent) == 1
        # Retry did not re-bill: one classify/research/compose in total.
        assert llm.classify_calls == 1
        assert llm.research_calls == 1
        assert llm.compose_calls == 1
        s.refresh(incident)
        assert incident.notified_at is not None


def test_poison_message_is_parked_after_max_attempts():
    init_db()
    with Session(engine) as s:
        _clean(s)
        llm = MailLLM()
        sent, deliver = _sent_collector()
        inbox = FakeInbox({6: raw_email(), 7: raw_email(message_id="<ok@x>")})
        inbox.broken_uids.add(6)
        settings = _settings(email_max_attempts=2)

        first = poll_inbox(
            s, settings, inbox_factory=lambda: inbox, llm=llm, deliver_fn=deliver
        )
        assert first.errors == 1 and first.stopped == "retryable failure"
        assert get_setting(s, LAST_UID_SETTING, "0") == "0"

        second = poll_inbox(
            s, settings, inbox_factory=lambda: inbox, llm=llm, deliver_fn=deliver
        )
        # Attempt 2 hits the cap: parked as error, queue moves on to uid 7.
        parked = s.exec(
            select(EmailMessage).where(EmailMessage.uid == 6)
        ).one()
        assert parked.status == "error" and "gave up" in parked.detail
        assert second.processed == 1
        assert get_setting(s, LAST_UID_SETTING) == "7"


def test_max_per_poll_defers_the_rest():
    init_db()
    with Session(engine) as s:
        _clean(s)
        llm = MailLLM(incident=False)
        sent, deliver = _sent_collector()
        inbox = FakeInbox(
            {i: raw_email(message_id=f"<m{i}@x>") for i in (1, 2, 3)}
        )
        settings = _settings(email_max_per_poll=1)

        first = poll_inbox(
            s, settings, inbox_factory=lambda: inbox, llm=llm, deliver_fn=deliver
        )
        assert first.new == 3 and first.not_incident == 1
        assert any("deferred" in n for n in first.notes)

        second = poll_inbox(
            s, settings, inbox_factory=lambda: inbox, llm=llm, deliver_fn=deliver
        )
        assert second.new == 2 and second.not_incident == 1


def test_uidvalidity_reset_dedupes_by_message_id():
    init_db()
    with Session(engine) as s:
        _clean(s)
        llm = MailLLM()
        sent, deliver = _sent_collector()
        message = raw_email(message_id="<stable@example.org>")

        poll_inbox(
            s, _settings(), inbox_factory=lambda: FakeInbox({50: message}),
            llm=llm, deliver_fn=deliver,
        )
        assert len(sent) == 1

        # Mailbox rebuilt: new UIDVALIDITY, same message now at UID 1.
        summary = poll_inbox(
            s, _settings(),
            inbox_factory=lambda: FakeInbox({1: message}, uidval=2),
            llm=llm, deliver_fn=deliver,
        )
        assert summary.skipped == 1 and len(sent) == 1
        assert llm.classify_calls == 1                      # no re-bill
        assert get_setting(s, UIDVALIDITY_SETTING) == "2"


def test_no_recipient_means_no_processing():
    init_db()
    with Session(engine) as s:
        _clean(s)
        llm = MailLLM()
        summary = poll_inbox(
            s, _settings(digest_to=""),
            inbox_factory=lambda: FakeInbox({1: raw_email()}),
            llm=llm,
        )
        assert "recipient" in summary.stopped
        assert llm.classify_calls == 0
