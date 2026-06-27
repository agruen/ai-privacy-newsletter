"""Tests for DB-backed settings: the Anthropic key store, resolver, and UI form."""

import re

from sqlmodel import Session

from app.config import get_settings
from app.db import engine, init_db
from app.models import Setting
from app.settings_store import (
    ANTHROPIC_KEY_SETTING,
    delete_setting,
    get_setting,
    resolve_anthropic_key,
    set_setting,
)


def _clear_key(session: Session) -> None:
    delete_setting(session, ANTHROPIC_KEY_SETTING)


def test_set_get_delete_roundtrip():
    init_db()
    with Session(engine) as session:
        _clear_key(session)
        assert get_setting(session, ANTHROPIC_KEY_SETTING) == ""
        set_setting(session, ANTHROPIC_KEY_SETTING, "  sk-ant-abc  ")
        assert get_setting(session, ANTHROPIC_KEY_SETTING) == "sk-ant-abc"  # stripped
        delete_setting(session, ANTHROPIC_KEY_SETTING)
        assert get_setting(session, ANTHROPIC_KEY_SETTING) == ""


def test_resolver_prefers_db_over_env():
    init_db()
    settings = get_settings().model_copy(update={"anthropic_api_key": "env-key"})
    with Session(engine) as session:
        _clear_key(session)
        # Falls back to the env value when nothing is stored.
        assert resolve_anthropic_key(session, settings) == "env-key"
        # A stored value wins.
        set_setting(session, ANTHROPIC_KEY_SETTING, "db-key")
        assert resolve_anthropic_key(session, settings) == "db-key"
        # Clearing it falls back to env again.
        delete_setting(session, ANTHROPIC_KEY_SETTING)
        assert resolve_anthropic_key(session, settings) == "env-key"


def _client_and_token():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    client.post(
        "/login",
        data={"username": "admin", "password": "supersecret-pw-123", "csrf_token": token},
    )
    return client, token


def test_settings_page_sets_and_clears_key():
    init_db()
    with Session(engine) as session:
        _clear_key(session)
    client, token = _client_and_token()

    # The page never leaks the value, only whether one is set.
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Anthropic API key" in page.text

    # Save a key via the form.
    resp = client.post(
        "/settings/anthropic-key",
        data={"api_key": "sk-ant-from-ui", "csrf_token": token},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with Session(engine) as session:
        assert session.get(Setting, ANTHROPIC_KEY_SETTING).value == "sk-ant-from-ui"
    # The status reflects "set via this page"; the secret itself is not rendered.
    assert "set via this page" in client.get("/settings").text
    assert "sk-ant-from-ui" not in client.get("/settings").text

    # Clear it again.
    client.post(
        "/settings/anthropic-key/clear",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    with Session(engine) as session:
        assert session.get(Setting, ANTHROPIC_KEY_SETTING) is None


def test_set_key_requires_csrf():
    init_db()
    with Session(engine) as session:
        _clear_key(session)
    client, _token = _client_and_token()
    client.post(
        "/settings/anthropic-key",
        data={"api_key": "sk-ant-nope", "csrf_token": "wrong"},
        follow_redirects=True,
    )
    with Session(engine) as session:
        assert session.get(Setting, ANTHROPIC_KEY_SETTING) is None
