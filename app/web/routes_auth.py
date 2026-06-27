"""Login / logout routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.auth import (
    authenticate,
    client_key,
    login_throttle,
    login_user,
    logout_user,
    verify_csrf,
)
from app.db import get_session
from app.web.templating import render

router = APIRouter()


@router.get("/login")
def login_form(request: Request):
    return render(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    session: Session = Depends(get_session),
):
    key = client_key(request)
    locked = login_throttle.seconds_locked(key)
    if locked:
        minutes = int(locked // 60) + 1
        return render(
            request,
            "login.html",
            {"error": f"Too many attempts. Try again in about {minutes} minute(s)."},
            status_code=429,
        )
    if not verify_csrf(request, csrf_token):
        return render(request, "login.html", {"error": "Session expired, try again."})
    user = authenticate(session, username, password)
    if not user:
        login_throttle.record_failure(key)
        return render(request, "login.html", {"error": "Invalid credentials."})
    login_throttle.reset(key)
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    if verify_csrf(request, csrf_token):
        logout_user(request)
    return RedirectResponse("/login", status_code=303)
