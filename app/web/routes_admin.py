"""Authenticated dashboard and settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.auth import hash_password, require_user, verify_csrf, verify_password
from app.config import get_settings
from app.db import get_session
from app.models import AppUser, Run
from app.web.templating import render

router = APIRouter()


@router.get("/")
def dashboard(
    request: Request,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    recent_runs = session.exec(
        select(Run).order_by(Run.started_at.desc()).limit(10)
    ).all()
    return render(request, "dashboard.html", {"recent_runs": recent_runs})


@router.get("/settings")
def settings_page(
    request: Request,
    user: AppUser = Depends(require_user),
):
    settings = get_settings()
    return render(
        request,
        "settings.html",
        {
            "settings": settings,
            "message": request.query_params.get("message"),
            "error": None,
        },
    )


@router.post("/settings/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    settings = get_settings()

    def fail(error: str):
        return render(
            request, "settings.html", {"settings": settings, "message": None, "error": error}
        )

    if not verify_csrf(request, csrf_token):
        return fail("Session expired, try again.")
    if not verify_password(user.password_hash, current_password):
        return fail("Current password is incorrect.")
    if len(new_password) < 12:
        return fail("New password must be at least 12 characters.")
    if new_password != confirm_password:
        return fail("New passwords do not match.")

    user.password_hash = hash_password(new_password)
    session.add(user)
    session.commit()
    return RedirectResponse("/settings?message=Password+updated", status_code=303)
