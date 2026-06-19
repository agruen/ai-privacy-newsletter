"""Browse ingested/classified incidents."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, func, select

from app.auth import require_user
from app.db import get_session
from app.models import AppUser, Incident, TaxonomyCategory
from app.web.templating import render

router = APIRouter()

STATUS_CHOICES = ["privacy", "all", "pending", "not_privacy", "needs_review"]


@router.get("/incidents")
def incidents_page(
    request: Request,
    status: str = "privacy",
    q: str = "",
    month: str = "",
    page: int = 1,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    page = max(1, page)
    per_page = 50
    stmt = select(Incident)
    if status == "privacy":
        stmt = stmt.where(Incident.is_privacy == True)  # noqa: E712
    elif status in ("pending", "not_privacy", "needs_review", "classified"):
        stmt = stmt.where(Incident.status == status)
    if q:
        stmt = stmt.where(Incident.title.contains(q))
    if month:  # YYYY-MM
        stmt = stmt.where(Incident.incident_date.startswith(month))

    total = session.exec(
        select(func.count()).select_from(stmt.subquery())
    ).one()
    rows = session.exec(
        stmt.order_by(Incident.incident_date.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()

    labels = {
        c.key: c.label
        for c in session.exec(select(TaxonomyCategory)).all()
    }
    incidents = [
        {
            "row": r,
            "categories": [labels.get(k, k) for k in json.loads(r.categories or "[]")],
        }
        for r in rows
    ]
    return render(
        request,
        "incidents.html",
        {
            "incidents": incidents,
            "status": status,
            "q": q,
            "month": month,
            "page": page,
            "per_page": per_page,
            "total": total,
            "status_choices": STATUS_CHOICES,
            "has_next": page * per_page < total,
        },
    )
