"""Sources admin: list sources, view incident counts, trigger ingest."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session, func, select

from app.auth import require_user, verify_csrf
from app.db import engine, get_session
from app.ingest.runner import run_ingest
from app.models import AppUser, Incident, Source
from app.progress import INGEST
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
            "progress": INGEST.snapshot(),
            "message": request.query_params.get("message"),
        },
    )


def run_ingest_job() -> None:
    """Run ingest + classify with its own DB session (background task).

    Drives the in-process INGEST tracker through its phases so the Sources page
    can show live progress via /sources/status.
    """
    from app.classify.runner import classify_pending

    try:
        with Session(engine) as session:
            summaries = run_ingest(session, INGEST)
            INGEST.update(phase="classifying", message="Classifying incidents…")
            classified = classify_pending(session)
            INGEST.update(classified=classified)
        parts = []
        for s in summaries:
            if s.error:
                parts.append(f"{s.source}: error: {s.error}")
            else:
                parts.append(
                    f"{s.source}: {s.note} (+{s.created} new, {s.updated} updated)"
                )
        detail = "; ".join(parts) + f"; classified {classified}"
        status = "error" if any(s.error for s in summaries) else "ok"
        INGEST.finish(status, detail)
    except Exception as exc:  # never leave the tracker stuck "active"
        logger.exception("manual ingest failed")
        INGEST.finish("error", "Ingest failed", str(exc))


@router.post("/sources/ingest")
def trigger_ingest(
    request: Request,
    background: BackgroundTasks,
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse("/sources?message=Session+expired", status_code=303)
    # One ingest at a time — a repeat click while one is running would double the
    # work (and the download) for no benefit.
    if INGEST.is_active():
        return RedirectResponse(
            "/sources?message=An+ingest+is+already+running", status_code=303
        )
    # Mark running synchronously so the page reflects it immediately, then run
    # after the response so a multi-minute snapshot download doesn't block.
    INGEST.start("Starting ingest…")
    background.add_task(run_ingest_job)
    return RedirectResponse("/sources", status_code=303)


@router.get("/sources/status")
def ingest_status(user: AppUser = Depends(require_user)) -> JSONResponse:
    """Live ingest progress for the Sources page poller."""
    return JSONResponse(INGEST.snapshot())
