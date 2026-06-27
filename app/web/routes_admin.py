"""Authenticated dashboard and settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, func, select

from app.auth import hash_password, require_user, verify_csrf, verify_password
from app.config import get_settings
from app.db import get_session
from app.llm import month_spend
from app.models import AppUser, Incident, Newsletter, Run
from app.settings_store import (
    ANTHROPIC_KEY_SETTING,
    delete_setting,
    get_setting,
    set_setting,
)
from app.web.templating import render

router = APIRouter()


def _settings_context(session: Session) -> dict:
    """Shared context for the settings page (key status, etc.)."""
    settings = get_settings()
    return {
        "settings": settings,
        # Whether an Anthropic key is configured, and from where, without ever
        # exposing the value itself.
        "ui_key_set": bool(get_setting(session, ANTHROPIC_KEY_SETTING)),
        "env_key_set": bool(settings.anthropic_api_key),
    }


@router.get("/")
def dashboard(
    request: Request,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    recent_runs = session.exec(
        select(Run).order_by(Run.started_at.desc()).limit(10)
    ).all()
    latest = session.exec(
        select(Newsletter).order_by(Newsletter.created_at.desc())
    ).first()
    privacy = session.exec(
        select(func.count()).select_from(Incident).where(Incident.is_privacy == True)  # noqa: E712
    ).one()
    pending = session.exec(
        select(func.count()).select_from(Incident).where(Incident.status == "pending")
    ).one()
    spend = month_spend(session)
    budget = settings.anthropic_monthly_budget_usd
    return render(
        request,
        "dashboard.html",
        {
            "recent_runs": recent_runs,
            "latest": latest,
            "privacy_count": privacy,
            "pending_count": pending,
            "spend": spend,
            "budget": budget,
            "spend_pct": min(100, round(spend / budget * 100)) if budget else 0,
        },
    )


@router.get("/settings")
def settings_page(
    request: Request,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    return render(
        request,
        "settings.html",
        {
            **_settings_context(session),
            "message": request.query_params.get("message"),
            "error": None,
        },
    )


@router.post("/settings/anthropic-key")
def set_anthropic_key(
    request: Request,
    api_key: str = Form(""),
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse(
            "/settings?message=Session+expired,+try+again", status_code=303
        )
    if not api_key.strip():
        return RedirectResponse("/settings?message=No+key+entered", status_code=303)
    set_setting(session, ANTHROPIC_KEY_SETTING, api_key)
    return RedirectResponse(
        "/settings?message=Anthropic+API+key+saved", status_code=303
    )


@router.post("/settings/anthropic-key/clear")
def clear_anthropic_key(
    request: Request,
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse(
            "/settings?message=Session+expired,+try+again", status_code=303
        )
    delete_setting(session, ANTHROPIC_KEY_SETTING)
    return RedirectResponse(
        "/settings?message=Stored+key+cleared+(environment+value+now+applies,+if+set)",
        status_code=303,
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
    def fail(error: str):
        return render(
            request,
            "settings.html",
            {**_settings_context(session), "message": None, "error": error},
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
