"""Security hardening tests: secret-key guard, URL sanitization, login throttle."""

from pydantic import ValidationError

from app.auth import LoginThrottle
from app.config import Settings
import json

from app.models import Incident
from app.synth.render import (
    build_context,
    render_html,
    render_markdown,
)


def test_production_rejects_weak_secret_key():
    for weak in ("", "change-me", "change-me-in-production", "tooshort"):
        try:
            Settings(environment="production", secret_key=weak)
            assert False, f"expected rejection of {weak!r}"
        except ValidationError:
            pass


def test_production_accepts_strong_secret_key():
    s = Settings(environment="production", secret_key="x" * 40)
    assert s.environment == "production"


def test_development_allows_default_secret_key():
    # Dev convenience: the weak default must not block local runs.
    s = Settings(environment="development", secret_key="change-me-in-production")
    assert s.environment == "development"


def _row(ext="x", headline="H"):
    return {
        "rows": [{
            "incident_external_id": ext, "headline": headline,
            "what_happened": "w", "risk_category": "C", "risk_explanation": "e",
        }]
    }


def _incident(url="", report_links=()):
    return Incident(
        source="aiid", external_id="x", dedup_key="aiid:x", title="t",
        description="d", url=url, incident_date="2026-05-03",
        raw_payload=json.dumps({"report_links": list(report_links)}),
    )


def test_render_drops_dangerous_incident_url():
    """A bad scheme on the incident link leaves the headline unlinked."""
    context = build_context([_incident(url="javascript:evil()")])
    content = _row()

    html = render_html(content, context)
    assert "javascript:" not in html
    assert "<a" not in html            # no anchor at all for a bad scheme
    assert "<strong>H</strong>" in html

    md = render_markdown(content, context)
    assert "javascript:" not in md
    assert "**H**" in md and "](" not in md


def test_render_drops_dangerous_source_links():
    context = build_context([_incident(
        url="https://incidentdatabase.ai/cite/1",
        report_links=[
            {"url": "javascript:alert(1)", "source_domain": "bad.test", "title": "Bad"},
            {"url": "https://example.org/a", "source_domain": "example.org", "title": "Good"},
        ],
    )])
    html = render_html(_row(), context)
    assert "javascript:" not in html and "bad.test" not in html
    assert 'href="https://example.org/a"' in html

    md = render_markdown(_row(), context)
    assert "javascript:" not in md and "bad.test" not in md
    assert "[example.org](https://example.org/a)" in md


def test_markdown_cells_cannot_break_the_table():
    """A pipe or newline in model output must not add or split columns."""
    content = {
        "rows": [{
            "incident_external_id": "x",
            "headline": "A | B",
            "what_happened": "line one\nline two | still one cell",
            "risk_category": "C|D", "risk_explanation": "e",
        }]
    }
    md = render_markdown(content, build_context([_incident()]))
    row = [ln for ln in md.splitlines() if ln.startswith("| ") and "A " in ln][0]
    # Five columns => six pipes; every literal pipe from the content is escaped.
    assert row.count("|") - row.count("\\|") == 6
    assert "\n" not in row


def test_login_throttle_locks_then_resets():
    t = LoginThrottle(max_failures=3, window=300.0, lockout=600.0)
    key = "1.2.3.4"
    assert t.seconds_locked(key) == 0.0
    for _ in range(3):
        t.record_failure(key)
    assert t.seconds_locked(key) > 0
    t.reset(key)
    assert t.seconds_locked(key) == 0.0
