"""The daily AIID check (noon Washington DC): ingest → screen new → digest.

The ingest step is the existing snapshot connector, which is deterministic and
cheap — AIID publishes weekly, so most days are a no-op ("no new snapshot").
LLM money is only spent on incidents never seen before:

- The first run establishes a baseline: everything already in the database is
  marked handled without email, so a fresh deploy doesn't blast the multi-year
  AIID backlog at the recipient.
- Later runs screen only the never-before-seen incidents for a privacy angle,
  write up the ones that pass (web-search research + house-format compose,
  cached on the incident), and send ONE digest email per run.

Incidents whose write-up fails, or that overflow the per-run cap, simply stay
unhandled and roll into the next day's run.
"""

from __future__ import annotations

import json
import logging

from sqlmodel import Session, select

from app.classify.runner import classify_pending
from app.config import Settings
from app.ingest.runner import run_ingest
from app.llm import (
    BudgetExceeded,
    LLMError,
    ensure_budget,
    get_llm,
    record_usage,
)
from app.mail.send import compose_digest_email, send_and_record
from app.mail.writer import (
    compose_writeup,
    incident_research_inputs,
    research_incident,
)
from app.models import Incident, utcnow
from app.settings_store import (
    get_setting,
    resolve_anthropic_key,
    resolve_digest_to,
    set_setting,
)
from app.synth.screen import screen_incidents

logger = logging.getLogger(__name__)

BASELINE_SETTING = "aiid_email_baseline"

_SALIENCE_ORDER = {"high": 0, "medium": 1, "low": 2, "": 3}


def _candidates(session: Session) -> list[Incident]:
    """Incidents the email channel hasn't decided about yet (email-sourced
    ones are handled individually by the inbox poll, not the digest)."""
    return session.exec(
        select(Incident)
        .where(Incident.source != "email")
        .where(Incident.notified_at == None)  # noqa: E711
    ).all()


def run_aiid_check(
    session: Session,
    settings: Settings,
    *,
    llm=None,
    deliver_fn=None,
) -> str:
    """One daily run; returns the human-readable detail for the Run row."""
    summaries = run_ingest(session)
    classified = classify_pending(session)
    ingest_note = "; ".join(
        f"{s.source}: {s.note}, +{s.created} new" + (f", ERR {s.error}" if s.error else "")
        for s in summaries
    ) or "no sources"
    parts = [ingest_note, f"classified {classified}"]

    if not get_setting(session, BASELINE_SETTING):
        suppressed = _establish_baseline(session)
        parts.append(f"baseline established — {suppressed} existing incidents suppressed")
        return "; ".join(parts)

    candidates = _candidates(session)
    if not candidates:
        parts.append("no new incidents")
        return "; ".join(parts)

    recipient = resolve_digest_to(session, settings)
    if not recipient:
        parts.append(f"{len(candidates)} new incidents waiting — recipient not configured")
        return "; ".join(parts)

    if llm is None:
        llm = get_llm(resolve_anthropic_key(session, settings))

    # Screen only the not-yet-screened newcomers for a privacy angle.
    if any(not c.llm_screened for c in candidates):
        ensure_budget(session, settings)
        screen_incidents(session, candidates, llm, settings)

    now = utcnow()
    privacy = [c for c in candidates if c.is_privacy or c.llm_privacy_angle]
    privacy_ids = {c.id for c in privacy}
    for other in candidates:
        if other.id not in privacy_ids:
            other.notified_at = now  # decided: no privacy angle, never emailed
            session.add(other)
    session.commit()
    parts.append(f"{len(candidates)} new, {len(privacy)} with a privacy angle")
    if not privacy:
        return "; ".join(parts)

    privacy.sort(
        key=lambda i: (_SALIENCE_ORDER.get(i.llm_salience, 3), i.incident_date),
    )
    selected = privacy[: settings.aiid_digest_max]
    if len(privacy) > len(selected):
        parts.append(f"{len(privacy) - len(selected)} deferred to the next run")

    ready: list[tuple[Incident, dict]] = []
    failures = 0
    for incident in selected:
        try:
            writeup = _writeup_for(session, settings, llm, incident)
        except BudgetExceeded as exc:
            parts.append(f"stopped: {exc}")
            break
        except LLMError as exc:
            failures += 1
            logger.warning("write-up failed for %s: %s", incident.dedup_key, exc)
            continue  # stays unhandled; retried on the next run
        ready.append((incident, writeup))
    if failures:
        parts.append(f"{failures} write-ups failed (will retry)")

    if not ready:
        parts.append("nothing ready to send")
        return "; ".join(parts)

    subject, text, html_body = compose_digest_email(ready)
    send_and_record(
        session, settings,
        kind="digest", to_addr=recipient, subject=subject,
        text=text, html_body=html_body,
        incident_ids=[i.id for i, _ in ready],
        deliver_fn=deliver_fn,
    )
    sent_at = utcnow()
    for incident, _ in ready:
        incident.notified_at = sent_at
        session.add(incident)
    session.commit()
    parts.append(f"digest sent to {recipient} with {len(ready)} write-ups")
    return "; ".join(parts)


def _establish_baseline(session: Session) -> int:
    """Mark every pre-existing incident handled so day one sends nothing."""
    now = utcnow()
    existing = session.exec(
        select(Incident).where(Incident.notified_at == None)  # noqa: E711
    ).all()
    for incident in existing:
        incident.notified_at = now
        session.add(incident)
    set_setting(session, BASELINE_SETTING, now.isoformat())
    session.commit()
    return len(existing)


def _writeup_for(
    session: Session, settings: Settings, llm, incident: Incident
) -> dict:
    """The incident's cached write-up, generating (and caching) it if absent."""
    if incident.writeup_json not in ("", "{}"):
        return json.loads(incident.writeup_json)

    inputs = incident_research_inputs(incident)
    ensure_budget(session, settings)
    try:
        notes, usage = research_incident(llm, settings, **inputs)
    except LLMError as exc:
        if exc.usage is not None:
            record_usage(
                session, purpose="aiid_research",
                model=settings.anthropic_model, usage=exc.usage,
            )
        raise
    record_usage(
        session, purpose="aiid_research", model=settings.anthropic_model, usage=usage
    )

    ensure_budget(session, settings)
    try:
        writeup, usage = compose_writeup(
            llm, settings,
            style_guide=get_setting(session, "style_guide") or None,
            title=inputs["title"], summary=inputs["summary"], body=inputs["body"],
            research_notes=notes, provenance=inputs["provenance"],
        )
    except LLMError as exc:
        if exc.usage is not None:
            record_usage(
                session, purpose="aiid_writeup",
                model=settings.anthropic_model, usage=exc.usage,
            )
        raise
    record_usage(
        session, purpose="aiid_writeup", model=settings.anthropic_model, usage=usage
    )

    incident.writeup_json = json.dumps(writeup)
    incident.writeup_model = settings.anthropic_model
    incident.writeup_at = utcnow()
    session.add(incident)
    session.commit()
    return writeup
