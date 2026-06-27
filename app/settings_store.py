"""Database-backed application settings (the ``Setting`` key/value table).

These are operator-editable values managed from the admin web UI, complementing
the environment-based :class:`app.config.Settings`. A value stored here takes
precedence over the corresponding environment default, so the admin can manage
it without editing ``.env`` and restarting — it takes effect on the next use.
"""

from __future__ import annotations

from sqlmodel import Session

from app.config import Settings
from app.models import Setting, utcnow

# Key under which the Anthropic API key is stored in the Setting table.
ANTHROPIC_KEY_SETTING = "anthropic_api_key"


def get_setting(session: Session, key: str, default: str = "") -> str:
    """Return the stored value for ``key``, or ``default`` if unset/empty."""
    row = session.get(Setting, key)
    return row.value if row and row.value.strip() else default


def set_setting(session: Session, key: str, value: str) -> None:
    """Create or update ``key`` with ``value`` (stripped)."""
    value = value.strip()
    row = session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
    else:
        row.value = value
        row.updated_at = utcnow()
    session.add(row)
    session.commit()


def delete_setting(session: Session, key: str) -> None:
    """Remove ``key`` if present (no-op otherwise)."""
    row = session.get(Setting, key)
    if row is not None:
        session.delete(row)
        session.commit()


def resolve_anthropic_key(session: Session, settings: Settings) -> str:
    """Effective Anthropic API key: a UI-set value wins over the env default."""
    return get_setting(session, ANTHROPIC_KEY_SETTING) or settings.anthropic_api_key
