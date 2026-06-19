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
    utcnow,
)
from app.scheduler import generate_for_period, previous_month
from app.synth.render import render_html, render_markdown, render_text
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


def _id_to_url(session: Session, newsletter_id: int) -> dict[str, str]:
    return {
        inc.external_id: inc.url
        for _item, inc in _items_with_incidents(session, newsletter_id)
    }


def _build_content_from_form(form) -> dict:
    """Reconstruct the structured content dict from the edit form."""
    def rows(prefix: str, fields: list[str]) -> list[dict]:
        out = []
        i = 0
        while True:
            key0 = f"{prefix}-{i}-{fields[0]}"
            if key0 not in form:
                break
            if form.get(f"{prefix}-{i}-remove"):
                i += 1
                continue
            row = {f: (form.get(f"{prefix}-{i}-{f}") or "").strip() for f in fields}
            i += 1
            out.append(row)
        return out

    featured = [
        r for r in rows(
            "featured",
            ["incident_external_id", "headline", "what_happened",
             "mechanism_failed", "regime_applies", "standard_of_care"],
        ) if r["headline"]
    ]
    briefs = [
        r for r in rows("brief", ["incident_external_id", "summary"]) if r["summary"]
    ]
    reading = [
        r for r in rows("reading", ["title", "url", "note"]) if r["title"]
    ]
    return {
        "editor_note": (form.get("editor_note") or "").strip(),
        "featured": featured,
        "brief_mentions": briefs,
        "recommended_reading": reading,
        "forward_look": (form.get("forward_look") or "").strip(),
    }


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
    return render(
        request,
        "newsletters.html",
        {
            "newsletters": newsletters,
            "default_period": previous_month(),
            "message": request.query_params.get("message"),
        },
    )


def _generate_job(period: str) -> None:
    try:
        generate_for_period(period)
    except Exception:
        logger.exception("manual generate failed for %s", period)


@router.post("/newsletters/generate")
def generate(
    request: Request,
    background: BackgroundTasks,
    period: str = Form(...),
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse("/newsletters?message=Session+expired", status_code=303)
    background.add_task(_generate_job, period.strip())
    return RedirectResponse(
        f"/newsletters?message=Generating+draft+for+{period.strip()}", status_code=303
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
    urls = _id_to_url(session, nid)
    return render(
        request,
        "newsletter_export.html",
        {
            "nl": nl,
            "markdown": render_markdown(content, urls),
            "html": render_html(content, urls),
            "text": render_text(content, urls),
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
    urls = _id_to_url(session, nid)
    renderers = {"md": render_markdown, "txt": render_text, "html": render_html}
    fmt = fmt if fmt in renderers else "md"
    body = renderers[fmt](content, urls)
    ext = {"md": "md", "txt": "txt", "html": "html"}[fmt]
    media = {"md": "text/markdown", "txt": "text/plain", "html": "text/html"}[fmt]
    filename = f"ai-privacy-digest-{nl.period}.{ext}"
    if fmt == "html":
        resp = HTMLResponse(body)
    else:
        resp = PlainTextResponse(body, media_type=media)
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp
