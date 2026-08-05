"""Outbound email: render write-ups, deliver over SMTP, record every send."""

from __future__ import annotations

import html
import json
import logging
import smtplib
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid

from sqlmodel import Session

from app.config import Settings
from app.models import Incident, OutboundEmail, utcnow

logger = logging.getLogger(__name__)

SUBJECT_PREFIX = "[AI Privacy Digest]"

_SECTIONS = (
    ("what_happened", "What happened"),
    ("mechanism_failed", "Mechanism that failed"),
    ("regime_applies", "Regulatory regime"),
    ("standard_of_care", "Standard of care"),
)


def _writeup_text(incident: Incident, writeup: dict, provenance: str) -> str:
    lines = [writeup.get("headline", incident.title), ""]
    for key, label in _SECTIONS:
        value = (writeup.get(key) or "").strip()
        if value:
            lines += [f"{label}: {value}", ""]
    corroboration = writeup.get("corroboration", "")
    note = (writeup.get("corroboration_note") or "").strip()
    if corroboration:
        lines.append(f"Corroboration: {corroboration}" + (f" — {note}" if note else ""))
    sources = writeup.get("sources") or []
    if sources:
        lines.append("Sources:")
        lines += [f"- {s.get('title', s.get('url', ''))} — {s.get('url', '')}"
                  for s in sources]
    lines += ["", f"Provenance: {provenance}"]
    return "\n".join(lines)


def _writeup_html(incident: Incident, writeup: dict, provenance: str) -> str:
    esc = html.escape
    parts = [f"<h2>{esc(writeup.get('headline', incident.title))}</h2>"]
    for key, label in _SECTIONS:
        value = (writeup.get(key) or "").strip()
        if value:
            parts.append(f"<p><strong>{label}:</strong> {esc(value)}</p>")
    corroboration = writeup.get("corroboration", "")
    note = (writeup.get("corroboration_note") or "").strip()
    if corroboration:
        parts.append(
            f"<p><strong>Corroboration:</strong> {esc(corroboration)}"
            + (f" — {esc(note)}" if note else "") + "</p>"
        )
    sources = writeup.get("sources") or []
    if sources:
        items = "".join(
            f'<li><a href="{esc(s.get("url", ""))}">'
            f'{esc(s.get("title") or s.get("url", ""))}</a></li>'
            for s in sources
        )
        parts.append(f"<p><strong>Sources:</strong></p><ul>{items}</ul>")
    parts.append(f"<p><em>Provenance: {esc(provenance)}</em></p>")
    return "\n".join(parts)


def incident_provenance(incident: Incident) -> str:
    if incident.source == "email":
        payload = json.loads(incident.raw_payload or "{}")
        sender = payload.get("from", "unknown sender")
        date = (incident.incident_date or "").strip()
        return f"submitted by email from {sender}" + (f" on {date}" if date else "")
    return f"AI Incident Database #{incident.external_id} — {incident.url}"


def compose_incident_email(incident: Incident, writeup: dict) -> tuple[str, str, str]:
    """(subject, text, html) for one incident write-up."""
    provenance = incident_provenance(incident)
    subject = f"{SUBJECT_PREFIX} {writeup.get('headline', incident.title)}"
    return subject, _writeup_text(incident, writeup, provenance), (
        "<html><body>" + _writeup_html(incident, writeup, provenance) + "</body></html>"
    )


def compose_digest_email(
    entries: list[tuple[Incident, dict]]
) -> tuple[str, str, str]:
    """(subject, text, html) for the daily AIID digest (one email per run)."""
    count = len(entries)
    date = utcnow().strftime("%Y-%m-%d")
    subject = (
        f"{SUBJECT_PREFIX} {count} new privacy incident"
        f"{'s' if count != 1 else ''} from the AI Incident Database ({date})"
    )
    text_parts, html_parts = [], []
    for incident, writeup in entries:
        provenance = incident_provenance(incident)
        text_parts.append(_writeup_text(incident, writeup, provenance))
        html_parts.append(_writeup_html(incident, writeup, provenance))
    text = ("\n\n" + "=" * 60 + "\n\n").join(text_parts)
    body_html = "<hr>".join(html_parts)
    return subject, text, f"<html><body>{body_html}</body></html>"


def deliver(settings: Settings, msg: MimeMessage) -> None:
    """Send one message over SMTP (SSL on port 465, otherwise STARTTLS)."""
    host = settings.resolved_smtp_host
    port = settings.smtp_port
    if port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=60)
    else:
        server = smtplib.SMTP(host, port, timeout=60)
        server.starttls()
    try:
        server.login(settings.resolved_smtp_username, settings.resolved_smtp_password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            logger.debug("SMTP quit failed", exc_info=True)


def send_and_record(
    session: Session,
    settings: Settings,
    *,
    kind: str,
    to_addr: str,
    subject: str,
    text: str,
    html_body: str,
    incident_ids: list[int],
    deliver_fn=None,
) -> OutboundEmail:
    """Deliver and log the attempt; raises after recording a failed send so the
    caller's retry logic kicks in (the write-up itself is already cached)."""
    msg = MimeMessage()
    msg["From"] = settings.resolved_mail_from
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid()
    msg["Auto-Submitted"] = "auto-generated"   # keep auto-responders quiet
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    row = OutboundEmail(
        kind=kind,
        to_addr=to_addr,
        subject=subject,
        body_excerpt=text[:500],
        incident_ids=json.dumps(incident_ids),
    )
    try:
        (deliver_fn or deliver)(settings, msg)
        row.status = "sent"
    except Exception as exc:
        row.status = "error"
        row.detail = str(exc)
        session.add(row)
        session.commit()
        raise
    session.add(row)
    session.commit()
    return row
