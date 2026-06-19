"""Authentication: password hashing, admin bootstrap, session helpers."""

from __future__ import annotations

import logging
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Request
from sqlmodel import Session, select

from app.config import get_settings
from app.db import engine, get_session
from app.models import AppUser

logger = logging.getLogger(__name__)
_hasher = PasswordHasher()

SESSION_USER_KEY = "user_id"
SESSION_CSRF_KEY = "csrf_token"


class NotAuthenticated(Exception):
    """Raised by require_user when no valid session is present."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:  # malformed hash, etc.
        return False


def bootstrap_admin() -> None:
    """Create the single admin from env on first run, if none exists yet."""
    settings = get_settings()
    with Session(engine) as session:
        existing = session.exec(select(AppUser)).first()
        if existing:
            return
        if not settings.admin_password:
            logger.warning(
                "No admin user and APN_ADMIN_PASSWORD is unset — "
                "set it and restart to create the admin account."
            )
            return
        user = AppUser(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
        )
        session.add(user)
        session.commit()
        logger.info("Created admin user %r from environment", settings.admin_username)


def authenticate(session: Session, username: str, password: str) -> AppUser | None:
    user = session.exec(select(AppUser).where(AppUser.username == username)).first()
    if user and verify_password(user.password_hash, password):
        return user
    return None


def login_user(request: Request, user: AppUser) -> None:
    request.session[SESSION_USER_KEY] = user.id


def logout_user(request: Request) -> None:
    request.session.clear()


def current_user(
    request: Request, session: Session = Depends(get_session)
) -> AppUser | None:
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    return session.get(AppUser, user_id)


def require_user(user: AppUser | None = Depends(current_user)) -> AppUser:
    if user is None:
        raise NotAuthenticated()
    return user


# --- CSRF ------------------------------------------------------------------

def get_csrf_token(request: Request) -> str:
    token = request.session.get(SESSION_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF_KEY] = token
    return token


def verify_csrf(request: Request, submitted: str | None) -> bool:
    expected = request.session.get(SESSION_CSRF_KEY)
    return bool(expected) and bool(submitted) and secrets.compare_digest(
        expected, submitted
    )
