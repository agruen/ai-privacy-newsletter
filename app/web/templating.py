"""Shared Jinja2 templates with common context injected."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import current_user, get_csrf_token
from app.config import get_settings
from app.db import engine
from sqlmodel import Session

_BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    settings = get_settings()
    ctx: dict[str, Any] = {
        "app_name": settings.app_name,
        "csrf_token": get_csrf_token(request),
    }
    # Resolve the current user for nav rendering.
    with Session(engine) as session:
        ctx["user"] = current_user(request, session)
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
