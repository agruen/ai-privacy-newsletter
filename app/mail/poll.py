"""The five-minute inbox poll: deterministic first, LLM only for new mail.

State is a (UIDVALIDITY, last UID) cursor in the Setting table. Each run:

1. lists UIDs above the cursor (pure IMAP, no model);
2. runs the deterministic gates (auto-reply/bounce/list headers, loop
   protection, Message-ID dedup) — most junk dies here for free;
3. only survivors get the paid pipeline: classify → research → compose →
   send to the configured recipient.

The cursor only advances past a message once it reaches a terminal state, so
a crash or a transient failure replays the message instead of losing it; the
write-up is cached on the incident so replays never re-bill completed LLM
work. A message that keeps failing is parked as `error` after
email_max_attempts so it cannot block the queue forever.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.config import Settings
from app.llm import (
    BudgetExceeded,
    LLMError,
    ensure_budget,
    get_llm,
    record_usage,
)
from app.mail.imap_client import ImapInbox
from app.mail.parse import ParsedEmail, parse_email
from app.mail.send import compose_incident_email, send_and_record
from app.mail.writer import classify_email, compose_writeup, research_incident
from app.models import EmailMessage, Incident, Run, utcnow
from app.settings_store import (
    get_setting,
    resolve_anthropic_key,
    resolve_digest_to,
    set_setting,
)

logger = logging.getLogger(__name__)

UIDVALIDITY_SETTING = "imap_uidvalidity"
LAST_UID_SETTING = "imap_last_uid"
LAST_POLL_AT_SETTING = "email_last_poll_at"
LAST_POLL_NOTE_SETTING = "email_last_poll_note"

_VALID_SALIENCE = {"high", "medium", "low"}


@dataclass
class PollSummary:
    new: int = 0            # UIDs above the cursor this run
    processed: int = 0      # write-ups sent
    skipped: int = 0        # deterministic skips + duplicates
    not_incident: int = 0
    errors: int = 0
    stopped: str = ""       # why the run ended early ("" = ran to completion)
    notes: list[str] = field(default_factory=list)

    @property
    def eventful(self) -> bool:
        return bool(self.new or self.errors or self.stopped)

    def detail(self) -> str:
        parts = [
            f"{self.new} new",
            f"{self.processed} processed",
            f"{self.not_incident} not-incident",
            f"{self.skipped} skipped",
        ]
        if self.errors:
            parts.append(f"{self.errors} errors")
        if self.stopped:
            parts.append(f"stopped: {self.stopped}")
        return "; ".join(parts + self.notes)


def _default_inbox(settings: Settings) -> ImapInbox:
    return ImapInbox(
        settings.imap_host,
        settings.imap_port,
        settings.imap_username,
        settings.imap_password,
        settings.imap_folder,
    )


def _row_for(session: Session, uid: int, folder: str) -> EmailMessage:
    row = session.exec(
        select(EmailMessage)
        .where(EmailMessage.uid == uid, EmailMessage.folder == folder)
        .order_by(EmailMessage.id.desc())
    ).first()
    return row or EmailMessage(uid=uid, folder=folder)


def _duplicate_of(
    session: Session, row: EmailMessage, message_id: str
) -> EmailMessage | None:
    if not message_id:
        return None
    return session.exec(
        select(EmailMessage).where(
            EmailMessage.message_id == message_id,
            EmailMessage.id != (row.id or -1),
            EmailMessage.status.in_(["processed", "not_incident", "skipped"]),
        )
    ).first()


def _external_id(parsed: ParsedEmail) -> str:
    """Stable id for the incident row; survives re-fetches of the same mail."""
    basis = parsed.message_id or (
        f"{parsed.uid}:{parsed.from_addr}:{parsed.subject}:{parsed.date_iso}"
    )
    return "em-" + hashlib.sha256(basis.encode()).hexdigest()[:16]


def _record_llm(session: Session, purpose: str, model: str, call):
    """Run an LLM call, recording spend even when the response is unusable."""
    try:
        result, usage = call()
    except LLMError as exc:
        if exc.usage is not None:
            record_usage(session, purpose=purpose, model=model, usage=exc.usage)
        raise
    record_usage(session, purpose=purpose, model=model, usage=usage)
    return result


def _create_incident(
    session: Session, settings: Settings, parsed: ParsedEmail, classified: dict
) -> Incident:
    external_id = _external_id(parsed)
    salience = (classified.get("salience") or "").lower()
    now = utcnow()
    model = settings.resolved_email_classify_model
    incident = Incident(
        source="email",
        external_id=external_id,
        dedup_key=f"email:{external_id}",
        title=(classified.get("title") or parsed.subject or "(untitled)").strip(),
        description=(classified.get("summary") or "").strip(),
        url=(classified.get("urls") or [""])[0],
        incident_date=(parsed.date_iso or now.isoformat())[:10],
        raw_payload=json.dumps(
            {
                "from": parsed.from_addr,
                "subject": parsed.subject,
                "message_id": parsed.message_id,
                "urls": classified.get("urls") or [],
                "body_excerpt": parsed.text[:4000],
            }
        ),
        status="classified",
        is_privacy=True,
        confidence=float(classified.get("confidence") or 0.0),
        decided_by=f"email_classifier:{model}",
        classified_at=now,
        llm_screened=True,
        llm_privacy_angle=True,
        llm_salience=salience if salience in _VALID_SALIENCE else "medium",
        llm_privacy_note=(classified.get("angle") or "").strip(),
        llm_screened_at=now,
        llm_screen_model=model,
    )
    session.add(incident)
    session.commit()
    session.refresh(incident)
    return incident


def _handle_uid(
    session: Session,
    settings: Settings,
    inbox,
    row: EmailMessage,
    uid: int,
    recipient: str,
    ensure_llm,
    deliver_fn,
) -> tuple[EmailMessage, str]:
    """Take one message to a terminal state. Returns (row, outcome)."""
    raw = inbox.fetch(uid)
    parsed = parse_email(
        uid, raw, max_bytes=settings.email_max_bytes,
        own_addr=settings.resolved_mail_from,
    )

    # After a UIDVALIDITY reset the same UID can name a different message;
    # keep the old record intact and log the new message on its own row.
    if (
        row.id is not None
        and row.message_id
        and parsed.message_id
        and row.message_id != parsed.message_id
    ):
        row = EmailMessage(uid=uid, folder=settings.imap_folder)

    row.message_id = parsed.message_id
    row.from_addr = parsed.from_addr
    row.subject = parsed.subject
    row.sent_at = parsed.date_iso
    row.size = parsed.size
    row.body_excerpt = parsed.text[:1500]
    row.updated_at = utcnow()

    if parsed.skip_reason:
        row.status, row.detail = "skipped", parsed.skip_reason
        return row, "skipped"

    duplicate = _duplicate_of(session, row, parsed.message_id)
    if duplicate is not None:
        row.status = "skipped"
        row.detail = f"duplicate of message #{duplicate.id} ({duplicate.status})"
        row.incident_id = duplicate.incident_id
        return row, "skipped"

    dedup_key = f"email:{_external_id(parsed)}"
    incident = session.exec(
        select(Incident).where(Incident.dedup_key == dedup_key)
    ).first()

    if incident is None or incident.writeup_json in ("", "{}"):
        llm = ensure_llm()

        ensure_budget(session, settings)
        classify_model = settings.resolved_email_classify_model
        classified = _record_llm(
            session, "email_classify", classify_model,
            lambda: classify_email(
                llm, settings,
                from_addr=parsed.from_addr, subject=parsed.subject,
                date_iso=parsed.date_iso, body=parsed.text,
            ),
        )
        if not classified.get("is_privacy_incident"):
            row.status = "not_incident"
            row.detail = (classified.get("reason") or "no privacy incident")[:300]
            return row, "not_incident"

        if incident is None:
            incident = _create_incident(session, settings, parsed, classified)
        row.incident_id = incident.id

        provenance = (
            f"email from {parsed.from_addr}"
            + (f" on {parsed.date_iso[:10]}" if parsed.date_iso else "")
        )
        title = classified.get("title") or parsed.subject
        summary = classified.get("summary") or ""
        urls = classified.get("urls") or []

        ensure_budget(session, settings)
        notes = _record_llm(
            session, "email_research", settings.anthropic_model,
            lambda: research_incident(
                llm, settings, title=title, summary=summary,
                body=parsed.text, urls=urls, provenance=provenance,
            ),
        )
        ensure_budget(session, settings)
        writeup = _record_llm(
            session, "email_writeup", settings.anthropic_model,
            lambda: compose_writeup(
                llm, settings,
                style_guide=get_setting(session, "style_guide") or None,
                title=title, summary=summary, body=parsed.text,
                research_notes=notes, provenance=provenance,
            ),
        )
        incident.writeup_json = json.dumps(writeup)
        incident.writeup_model = settings.anthropic_model
        incident.writeup_at = utcnow()
        session.add(incident)
        session.commit()
        session.refresh(incident)
    else:
        row.incident_id = incident.id

    writeup = json.loads(incident.writeup_json)
    if incident.notified_at is None:
        subject, text, html_body = compose_incident_email(incident, writeup)
        send_and_record(
            session, settings,
            kind="incident", to_addr=recipient, subject=subject,
            text=text, html_body=html_body, incident_ids=[incident.id],
            deliver_fn=deliver_fn,
        )
        incident.notified_at = utcnow()
        session.add(incident)
        session.commit()

    row.status = "processed"
    row.detail = (writeup.get("headline") or incident.title)[:200]
    return row, "processed"


def poll_inbox(
    session: Session,
    settings: Settings,
    *,
    inbox_factory=None,
    llm=None,
    deliver_fn=None,
) -> PollSummary:
    """One poll run. Records a Run row only when something actually happened
    (288 no-op rows a day would drown the dashboard); the last-poll heartbeat
    lives in the Setting table either way."""
    summary = PollSummary()

    recipient = resolve_digest_to(session, settings)
    if not recipient:
        summary.stopped = "digest recipient not configured"
        _stamp(session, summary.stopped)
        return summary

    state = {"llm": llm}

    def ensure_llm():
        if state["llm"] is None:
            state["llm"] = get_llm(resolve_anthropic_key(session, settings))
        return state["llm"]

    factory = inbox_factory or (lambda: _default_inbox(settings))
    with factory() as inbox:
        uidvalidity = inbox.uidvalidity()
        last_uid = int(get_setting(session, LAST_UID_SETTING, "0") or 0)
        if get_setting(session, UIDVALIDITY_SETTING) != str(uidvalidity):
            # UIDs were reassigned (or this is the first run): restart the
            # cursor. Message-ID dedup keeps old mail from reprocessing.
            last_uid = 0
            set_setting(session, UIDVALIDITY_SETTING, str(uidvalidity))
            set_setting(session, LAST_UID_SETTING, "0")

        uids = inbox.uids_above(last_uid)
        summary.new = len(uids)
        batch = uids[: settings.email_max_per_poll]
        if len(uids) > len(batch):
            summary.notes.append(f"{len(uids) - len(batch)} deferred to next poll")

        for uid in batch:
            row = _row_for(session, uid, settings.imap_folder)
            try:
                row, outcome = _handle_uid(
                    session, settings, inbox, row, uid,
                    recipient, ensure_llm, deliver_fn,
                )
            except BudgetExceeded as exc:
                # Nothing was spent (the guard checks before calling); leave
                # the message for a poll after the budget frees up.
                summary.stopped = str(exc)
                break
            except Exception as exc:
                logger.exception("email uid %s failed", uid)
                summary.errors += 1
                row.attempts += 1
                row.updated_at = utcnow()
                if row.attempts >= settings.email_max_attempts:
                    row.status = "error"
                    row.detail = f"gave up after {row.attempts} attempts: {exc}"
                    session.add(row)
                    session.commit()
                    set_setting(session, LAST_UID_SETTING, str(uid))
                    continue
                row.status = "received"
                row.detail = f"attempt {row.attempts} failed: {exc} — will retry"
                session.add(row)
                session.commit()
                summary.stopped = "retryable failure"
                break  # preserve order: don't advance past a retryable failure
            session.add(row)
            session.commit()
            set_setting(session, LAST_UID_SETTING, str(uid))
            if outcome == "processed":
                summary.processed += 1
            elif outcome == "not_incident":
                summary.not_incident += 1
            else:
                summary.skipped += 1

    _stamp(session, summary.detail())
    if summary.eventful:
        session.add(
            Run(
                job="email_poll",
                status="error" if summary.errors else "ok",
                detail=summary.detail(),
                finished_at=utcnow(),
            )
        )
        session.commit()
    return summary


def _stamp(session: Session, note: str) -> None:
    set_setting(session, LAST_POLL_AT_SETTING, utcnow().isoformat())
    set_setting(session, LAST_POLL_NOTE_SETTING, note)
