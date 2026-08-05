"""Tests for inbound-email parsing and the deterministic triage gates."""

from email.message import EmailMessage as Mime

from app.mail.parse import html_to_text, parse_email


def raw_email(
    *,
    from_addr="alice@example.org",
    subject="An incident",
    body="Something happened.",
    html=None,
    headers=None,
    message_id="<m1@example.org>",
    date="Tue, 05 Aug 2026 09:30:00 -0400",
) -> bytes:
    msg = Mime()
    msg["From"] = from_addr
    msg["Subject"] = subject
    if message_id:
        msg["Message-ID"] = message_id
    if date:
        msg["Date"] = date
    for key, value in (headers or {}).items():
        msg[key] = value
    if html is not None:
        msg.set_content(body)
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(body)
    return msg.as_bytes()


def test_plain_text_email_parses():
    parsed = parse_email(7, raw_email(), max_bytes=10_000)
    assert parsed.uid == 7
    assert parsed.from_addr == "alice@example.org"
    assert parsed.subject == "An incident"
    assert parsed.message_id == "<m1@example.org>"
    assert "Something happened." in parsed.text
    assert parsed.date_iso.startswith("2026-08-05")
    assert parsed.skip_reason == ""


def test_html_only_email_falls_back_to_text():
    msg = Mime()
    msg["From"] = "bob@example.org"
    msg["Subject"] = "HTML report"
    msg.add_alternative(
        "<html><head><style>p{color:red}</style></head>"
        "<body><p>First line</p><p>Second <b>line</b></p></body></html>",
        subtype="html",
    )
    parsed = parse_email(1, msg.as_bytes(), max_bytes=10_000)
    assert "First line" in parsed.text
    assert "Second line" in parsed.text
    assert "color:red" not in parsed.text


def test_html_to_text_strips_script_and_keeps_breaks():
    text = html_to_text("<script>alert(1)</script><p>a</p><p>b</p>")
    assert "alert" not in text
    assert text.splitlines() == ["a", "b"]


def test_auto_submitted_is_skipped():
    parsed = parse_email(
        1,
        raw_email(headers={"Auto-Submitted": "auto-replied"}),
        max_bytes=10_000,
    )
    assert "auto-submitted" in parsed.skip_reason


def test_bulk_precedence_and_list_are_skipped():
    bulk = parse_email(1, raw_email(headers={"Precedence": "bulk"}), max_bytes=100)
    assert "precedence" in bulk.skip_reason
    lst = parse_email(
        2, raw_email(headers={"List-Id": "<x.example.org>"}), max_bytes=100
    )
    assert "mailing-list" in lst.skip_reason


def test_bounce_sender_is_skipped():
    parsed = parse_email(
        1, raw_email(from_addr="MAILER-DAEMON@mx.example.org"), max_bytes=100
    )
    assert "bounce" in parsed.skip_reason


def test_own_address_is_skipped_for_loop_protection():
    parsed = parse_email(
        1,
        raw_email(from_addr="digest@example.org"),
        max_bytes=100,
        own_addr="Digest@Example.org",
    )
    assert "loop protection" in parsed.skip_reason


def test_empty_message_is_skipped_but_subject_only_is_kept():
    empty = parse_email(1, raw_email(subject="", body=""), max_bytes=100)
    assert empty.skip_reason == "empty message"
    subject_only = parse_email(2, raw_email(subject="FYI", body=""), max_bytes=100)
    assert subject_only.skip_reason == ""


def test_body_is_truncated_to_max_bytes():
    parsed = parse_email(1, raw_email(body="x" * 500), max_bytes=100)
    assert len(parsed.text) == 100
