"""Newsletter review UI: list, generate, view/edit, approve, export."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlmodel import Session, select

from app.auth import require_user, verify_csrf
from app.config import get_settings
from app.db import engine, get_session
from app.models import (
    AppUser,
    Incident,
    MemberFlag,
    Newsletter,
    NewsletterItem,
    Run,
    utcnow,
)
from app.scheduler import generate_for_period, previous_month, record_run
from app.synth.compose import NewsletterExists, latest_for_period
from app.synth.render import (
    build_context,
    format_date,
    render_html,
    render_markdown,
    render_text,
)
from app.web.templating import render

logger = logging.getLogger(__name__)
router = APIRouter()


# -- helpers ---------------------------------------------------------------

def _items_with_incidents(session: Session, newsletter_id: int):
    rows = session.exec(
        select(NewsletterItem, Incident)
        .where(NewsletterItem.newsletter_id == newsletter_id)
        .where(NewsletterItem.incident_id == Incident.id)
        .order_by(NewsletterItem.role, NewsletterItem.rank)
    ).all()
    return rows


def _row_context(session: Session, newsletter_id: int):
    """The data-derived half of every row: date, number, and the links."""
    return build_context(
        [inc for _item, inc in _items_with_incidents(session, newsletter_id)],
        get_settings().row_sources_max,
    )


def _build_content_from_form(form) -> dict:
    """Reconstruct the structured content dict from the edit form."""
    rows = []
    i = 0
    fields = [
        "incident_external_id", "headline", "what_happened",
        "risk_category", "risk_explanation",
    ]
    while f"row-{i}-headline" in form:
        if form.get(f"row-{i}-remove"):
            i += 1
            continue
        row = {f: (form.get(f"row-{i}-{f}") or "").strip() for f in fields}
        i += 1
        if row["headline"]:
            rows.append(row)
    return {"rows": rows}


# -- routes ----------------------------------------------------------------

@router.get("/newsletters")
def list_page(
    request: Request,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    newsletters = session.exec(
        select(Newsletter).order_by(Newsletter.created_at.desc())
    ).all()
    # Generation runs in the background, so the "Generating..." message above is
    # only ever a "started". Surface the last outcome so a failed run is visible
    # here on reload instead of living only in the container log.
    last_run = session.exec(
        select(Run)
        .where(Run.job == "manual_generate")
        .order_by(Run.id.desc())
        .limit(1)
    ).first()
    return render(
        request,
        "newsletters.html",
        {
            "newsletters": newsletters,
            "default_period": previous_month(),
            "message": request.query_params.get("message"),
            "last_run": last_run,
        },
    )


def _generate_job(period: str, regenerate: bool = False, rescreen: bool = False) -> None:
    """Run a manual generation, recording the outcome as a Run row.

    This runs as a background task, so the redirect has already been sent and a
    raised exception would reach nothing but the log. Recording the outcome puts
    failures on the dashboard next to the scheduled jobs, where an operator will
    actually see them.
    """
    try:
        detail = generate_for_period(period, regenerate=regenerate, rescreen=rescreen)
        record_run("manual_generate", "ok", detail)
    except NewsletterExists:
        logger.info("generate skipped for %s: draft already exists", period)
        record_run(
            "manual_generate", "ok", f"{period}: draft already exists, skipped"
        )
    except Exception as exc:
        logger.exception("manual generate failed for %s", period)
        record_run("manual_generate", "error", f"{period}: {exc}")


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "on", "yes")


@router.post("/newsletters/generate")
def generate(
    request: Request,
    background: BackgroundTasks,
    period: str = Form(...),
    regenerate: str = Form(""),
    rescreen: str = Form(""),
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse("/newsletters?message=Session+expired", status_code=303)
    period = period.strip()
    regen = _truthy(regenerate)
    rescr = _truthy(rescreen)

    # Refuse a duplicate up front so a repeat click doesn't bill a second draft.
    existing = latest_for_period(session, period)
    if existing and not regen:
        return RedirectResponse(
            f"/newsletters?message=A+draft+for+{period}+already+exists.+"
            "Open+it,+or+tick+Replace+to+regenerate.",
            status_code=303,
        )

    background.add_task(_generate_job, period, regen, rescr)
    verb = "Regenerating" if regen else "Generating"
    return RedirectResponse(
        f"/newsletters?message={verb}+draft+for+{period}", status_code=303
    )


@router.get("/newsletters/{nid}")
def detail(
    nid: int,
    request: Request,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    nl = session.get(Newsletter, nid)
    if not nl:
        return RedirectResponse("/newsletters?message=Not+found", status_code=303)
    content = json.loads(nl.content_json or "{}")
    flags = session.exec(
        select(MemberFlag).where(MemberFlag.newsletter_id == nid)
    ).all()
    pool = [
        inc for item, inc in _items_with_incidents(session, nid) if item.role == "pool"
    ]
    return render(
        request,
        "newsletter_detail.html",
        {
            "nl": nl,
            "content": content,
            "flags": flags,
            "pool": pool,
            "row_ctx": _row_context(session, nid),
            "format_date": format_date,
            "message": request.query_params.get("message"),
        },
    )


@router.post("/newsletters/{nid}/edit")
async def edit(
    nid: int,
    request: Request,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    nl = session.get(Newsletter, nid)
    if not nl:
        return RedirectResponse("/newsletters?message=Not+found", status_code=303)
    form = await request.form()
    if not verify_csrf(request, form.get("csrf_token")):
        return RedirectResponse(f"/newsletters/{nid}?message=Session+expired", status_code=303)
    if nl.status == "approved":
        return RedirectResponse(f"/newsletters/{nid}?message=Reopen+to+edit", status_code=303)
    nl.content_json = json.dumps(_build_content_from_form(form))
    session.add(nl)
    session.commit()
    return RedirectResponse(f"/newsletters/{nid}?message=Saved", status_code=303)


@router.post("/newsletters/{nid}/approve")
def approve(
    nid: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    nl = session.get(Newsletter, nid)
    if nl and verify_csrf(request, csrf_token):
        nl.status = "approved"
        nl.approved_at = utcnow()
        session.add(nl)
        session.commit()
    return RedirectResponse(f"/newsletters/{nid}?message=Approved", status_code=303)


@router.post("/newsletters/{nid}/reopen")
def reopen(
    nid: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    nl = session.get(Newsletter, nid)
    if nl and verify_csrf(request, csrf_token):
        nl.status = "draft"
        nl.approved_at = None
        session.add(nl)
        session.commit()
    return RedirectResponse(f"/newsletters/{nid}?message=Reopened", status_code=303)


@router.get("/newsletters/{nid}/export")
def export_page(
    nid: int,
    request: Request,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    nl = session.get(Newsletter, nid)
    if not nl:
        return RedirectResponse("/newsletters?message=Not+found", status_code=303)
    content = json.loads(nl.content_json or "{}")
    context = _row_context(session, nid)
    return render(
        request,
        "newsletter_export.html",
        {
            "nl": nl,
            "markdown": render_markdown(content, context),
            "html": render_html(content, context),
            "text": render_text(content, context),
        },
    )


@router.get("/newsletters/{nid}/download")
def download(
    nid: int,
    fmt: str = "md",
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    nl = session.get(Newsletter, nid)
    if not nl:
        return PlainTextResponse("Not found", status_code=404)
    content = json.loads(nl.content_json or "{}")
    context = _row_context(session, nid)
    renderers = {"md": render_markdown, "txt": render_text, "html": render_html}
    fmt = fmt if fmt in renderers else "md"
    body = renderers[fmt](content, context)
    ext = {"md": "md", "txt": "txt", "html": "html"}[fmt]
    media = {"md": "text/markdown", "txt": "text/plain", "html": "text/html"}[fmt]
    filename = f"ai-privacy-digest-{nl.period}.{ext}"
    if fmt == "html":
        resp = HTMLResponse(body)
    else:
        resp = PlainTextResponse(body, media_type=media)
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp
