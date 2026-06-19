"""Taxonomy category admin (CRUD)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.auth import require_user, verify_csrf
from app.db import get_session
from app.models import AppUser, TaxonomyCategory
from app.web.templating import render

router = APIRouter()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


@router.get("/taxonomy")
def taxonomy_page(
    request: Request,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    cats = session.exec(
        select(TaxonomyCategory).order_by(TaxonomyCategory.sort_order)
    ).all()
    return render(
        request,
        "taxonomy.html",
        {"categories": cats, "message": request.query_params.get("message")},
    )


@router.post("/taxonomy/add")
def add_category(
    request: Request,
    label: str = Form(...),
    description: str = Form(""),
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse("/taxonomy?message=Session+expired", status_code=303)
    key = _slug(label)
    if key and session.get(TaxonomyCategory, key) is None:
        n = len(session.exec(select(TaxonomyCategory)).all())
        session.add(
            TaxonomyCategory(key=key, label=label.strip(), description=description.strip(), sort_order=n)
        )
        session.commit()
    return RedirectResponse("/taxonomy?message=Category+added", status_code=303)


@router.post("/taxonomy/{key}/update")
def update_category(
    key: str,
    request: Request,
    label: str = Form(...),
    description: str = Form(""),
    enabled: str = Form(""),
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse("/taxonomy?message=Session+expired", status_code=303)
    cat = session.get(TaxonomyCategory, key)
    if cat:
        cat.label = label.strip()
        cat.description = description.strip()
        cat.enabled = enabled == "on"
        session.add(cat)
        session.commit()
    return RedirectResponse("/taxonomy?message=Saved", status_code=303)


@router.post("/taxonomy/{key}/delete")
def delete_category(
    key: str,
    request: Request,
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse("/taxonomy?message=Session+expired", status_code=303)
    cat = session.get(TaxonomyCategory, key)
    if cat:
        session.delete(cat)
        session.commit()
    return RedirectResponse("/taxonomy?message=Deleted", status_code=303)
