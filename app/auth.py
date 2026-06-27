"""Authentication: password hashing, admin bootstrap, session helpers."""

from __future__ import annotations

import logging
import secrets
import threading
import time

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


# --- Login throttling ------------------------------------------------------

class LoginThrottle:
    """In-process failed-login limiter with temporary lockout.

    This is a single-admin, single-process app, so an in-memory limiter keyed by
    client IP is enough and avoids a new dependency. After ``max_failures`` failed
    attempts within ``window`` seconds, the key is locked for ``lockout`` seconds.
    A successful login clears the key.
    """

    def __init__(
        self, max_failures: int = 5, window: float = 300.0, lockout: float = 900.0
    ) -> None:
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def seconds_locked(self, key: str) -> float:
        """Seconds until the key is allowed to try again (0 if not locked)."""
        now = time.monotonic()
        with self._lock:
            return max(0.0, self._locked_until.get(key, 0.0) - now)

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._failures.get(key, []) if now - t < self.window]
            hits.append(now)
            if len(hits) >= self.max_failures:
                self._locked_until[key] = now + self.lockout
                self._failures[key] = []
            else:
                self._failures[key] = hits

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


login_throttle = LoginThrottle()


def client_key(request: Request) -> str:
    """Identify the caller for throttling (best-effort client IP)."""
    client = request.client
    return client.host if client else "unknown"
