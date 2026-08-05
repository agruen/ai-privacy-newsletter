"""Parsing and deterministic triage of raw inbound email.

Everything here is pure and offline: RFC 5322 parsing, plain-text extraction
(with an HTML fallback), and the header checks that let the poller discard
bounces, auto-responders, and our own outbound mail without spending a model
call. Only messages that pass this gate ever reach the LLM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# Senders that are machinery, not people: bounce and postmaster traffic.
_BOUNCE_PREFIXES = ("mailer-daemon@", "postmaster@")

# Precedence header values that mark automated/bulk traffic.
_AUTO_PRECEDENCE = {"bulk", "junk", "list", "auto_reply"}

_SUPPRESSED_HTML_TAGS = {"style", "script", "head", "title", "template"}
_BREAK_HTML_TAGS = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "blockquote"}


@dataclass
class ParsedEmail:
    """One inbound message, reduced to what the pipeline needs."""

    uid: int
    message_id: str
    from_addr: str
    subject: str
    date_iso: str            # Date header as ISO 8601, "" if unparseable
    text: str                # plain-text body (possibly derived from HTML), truncated
    size: int                # raw message size in bytes
    skip_reason: str = ""    # non-empty -> deterministic skip, no LLM call


class _HTMLText(HTMLParser):
    """Minimal HTML→text: drop script/style, keep block-level line breaks."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._suppress = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SUPPRESSED_HTML_TAGS:
            self._suppress += 1
        elif tag in _BREAK_HTML_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SUPPRESSED_HTML_TAGS and self._suppress:
            self._suppress -= 1

    def handle_data(self, data: str) -> None:
        if not self._suppress:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(html)
    except Exception:  # malformed HTML from the wild — keep whatever we got
        logger.debug("HTML parse failed; using partial text", exc_info=True)
    lines = [line.strip() for line in parser.text().splitlines()]
    return "\n".join(line for line in lines if line)


def _body_text(msg) -> str:
    """Best-effort plain text: prefer text/plain, fall back to text/html."""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:
        part = None
    if part is None:
        return ""
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    if part.get_content_subtype() == "html":
        return html_to_text(content)
    return content


def _skip_reason(msg, from_addr: str, own_addr: str) -> str:
    """A non-empty reason means: automated traffic, drop without an LLM call."""
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return f"auto-submitted ({auto})"
    precedence = (msg.get("Precedence") or "").strip().lower()
    if precedence in _AUTO_PRECEDENCE:
        return f"precedence: {precedence}"
    if msg.get("List-Id"):
        return "mailing-list traffic (List-Id)"
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return "auto-reply header"
    sender = from_addr.lower()
    if any(sender.startswith(p) for p in _BOUNCE_PREFIXES):
        return "bounce/postmaster sender"
    if own_addr and sender == own_addr.lower():
        return "own address (loop protection)"
    return ""


def parse_email(
    uid: int, raw: bytes, *, max_bytes: int, own_addr: str = ""
) -> ParsedEmail:
    """Parse one raw RFC 5322 message and run the deterministic triage gates."""
    msg = message_from_bytes(raw, policy=policy.default)

    from_addr = parseaddr(str(msg.get("From") or ""))[1]
    subject = str(msg.get("Subject") or "").strip()
    message_id = str(msg.get("Message-ID") or "").strip()

    date_iso = ""
    try:
        parsed_date = parsedate_to_datetime(str(msg.get("Date")))
        if parsed_date is not None:
            date_iso = parsed_date.isoformat()
    except Exception:
        pass

    text = _body_text(msg).strip()
    if len(text) > max_bytes:
        text = text[:max_bytes]

    reason = _skip_reason(msg, from_addr, own_addr)
    if not reason and not text and not subject:
        reason = "empty message"

    return ParsedEmail(
        uid=uid,
        message_id=message_id,
        from_addr=from_addr,
        subject=subject,
        date_iso=date_iso,
        text=text,
        size=len(raw),
        skip_reason=reason,
    )
