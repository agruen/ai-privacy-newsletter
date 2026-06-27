"""Security hardening tests: secret-key guard, URL sanitization, login throttle."""

from pydantic import ValidationError

from app.auth import LoginThrottle
from app.config import Settings
from app.synth.render import render_html, render_markdown


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


def _content_with_reading(urls):
    return {
        "editor_note": "n",
        "featured": [],
        "brief_mentions": [],
        "recommended_reading": [
            {"title": t, "url": u, "note": ""} for t, u in urls
        ],
        "forward_look": "f",
    }


def test_render_drops_dangerous_reading_urls():
    content = _content_with_reading(
        [("Bad", "javascript:alert(1)"), ("Good", "https://example.org")]
    )
    html = render_html(content, {})
    assert "javascript:" not in html
    assert 'href="https://example.org"' in html      # good link kept
    assert ">Good</a>" in html
    assert "Bad" in html and ">Bad</a>" not in html   # bad one is plain text

    md = render_markdown(content, {})
    assert "javascript:" not in md
    assert "(https://example.org)" in md


def test_render_sanitizes_incident_source_url():
    content = {
        "editor_note": "",
        "featured": [{
            "incident_external_id": "x", "headline": "H",
            "what_happened": "", "mechanism_failed": "",
            "regime_applies": "", "standard_of_care": "",
        }],
        "brief_mentions": [],
        "recommended_reading": [],
        "forward_look": "",
    }
    html = render_html(content, {"x": "javascript:evil()"})
    assert "javascript:" not in html
    assert "(<a" not in html   # no source anchor emitted for a bad scheme


def test_login_throttle_locks_then_resets():
    t = LoginThrottle(max_failures=3, window=300.0, lockout=600.0)
    key = "1.2.3.4"
    assert t.seconds_locked(key) == 0.0
    for _ in range(3):
        t.record_failure(key)
    assert t.seconds_locked(key) > 0
    t.reset(key)
    assert t.seconds_locked(key) == 0.0
