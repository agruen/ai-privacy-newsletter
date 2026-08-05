"""Activity log: every inbound email, outbound send, and email-channel run.

This is the operator's review surface for the email channel — nothing the
poller sees is dropped silently, so this page is the complete record of what
arrived, what the app decided, and what it sent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, func, select

from app.auth import require_user
from app.config import get_settings
from app.db import get_session
from app.models import AppUser, EmailMessage, OutboundEmail, Run
from app.settings_store import get_setting, resolve_digest_to
from app.web.templating import render

router = APIRouter()

_EMAIL_JOBS = ("email_poll", "aiid_check")


@router.get("/activity")
def activity_page(
    request: Request,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    emails = session.exec(
        select(EmailMessage).order_by(EmailMessage.id.desc()).limit(200)
    ).all()
    outbound = session.exec(
        select(OutboundEmail).order_by(OutboundEmail.id.desc()).limit(100)
    ).all()
    runs = session.exec(
        select(Run)
        .where(Run.job.in_(_EMAIL_JOBS))
        .order_by(Run.id.desc())
        .limit(50)
    ).all()
    status_counts = dict(
        session.exec(
            select(EmailMessage.status, func.count()).group_by(EmailMessage.status)
        ).all()
    )
    return render(
        request,
        "activity.html",
        {
            "emails": emails,
            "outbound": outbound,
            "runs": runs,
            "status_counts": status_counts,
            "email_configured": settings.email_configured,
            "recipient": resolve_digest_to(session, settings),
            "last_poll_at": get_setting(session, "email_last_poll_at"),
            "last_poll_note": get_setting(session, "email_last_poll_note"),
        },
    )
