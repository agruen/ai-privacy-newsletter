"""Sources admin: list sources, view incident counts, trigger ingest."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, func, select

from app.auth import require_user, verify_csrf
from app.db import engine, get_session
from app.ingest.runner import run_ingest
from app.models import AppUser, Incident, Source
from app.web.templating import render

logger = logging.getLogger(__name__)
router = APIRouter()


def _counts(session: Session) -> dict[str, int]:
    total = session.exec(select(func.count()).select_from(Incident)).one()
    privacy = session.exec(
        select(func.count()).select_from(Incident).where(Incident.is_privacy == True)  # noqa: E712
    ).one()
    pending = session.exec(
        select(func.count()).select_from(Incident).where(Incident.status == "pending")
    ).one()
    return {"total": total, "privacy": privacy, "pending": pending}


@router.get("/sources")
def sources_page(
    request: Request,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    sources = session.exec(select(Source)).all()
    return render(
        request,
        "sources.html",
        {
            "sources": sources,
            "counts": _counts(session),
            "message": request.query_params.get("message"),
        },
    )


def run_ingest_job() -> None:
    """Run ingest with its own DB session (used as a background task)."""
    with Session(engine) as session:
        run_ingest(session)


@router.post("/sources/ingest")
def trigger_ingest(
    request: Request,
    background: BackgroundTasks,
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse("/sources?message=Session+expired", status_code=303)
    # Run after the response so a multi-minute snapshot download doesn't block.
    background.add_task(run_ingest_job)
    return RedirectResponse(
        "/sources?message=Ingest+started+in+the+background", status_code=303
    )
