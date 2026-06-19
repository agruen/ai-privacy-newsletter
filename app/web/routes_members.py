"""FPF member list admin: CRUD + CSV import."""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.auth import require_user, verify_csrf
from app.db import get_session
from app.models import AppUser, Member
from app.web.templating import render

router = APIRouter()


def _parse_aliases(raw: str) -> str:
    parts = [a.strip() for a in (raw or "").replace("|", ";").split(";")]
    return json.dumps([a for a in parts if a])


@router.get("/members")
def members_page(
    request: Request,
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    members = session.exec(select(Member).order_by(Member.name)).all()
    rows = [
        {"m": m, "aliases": ", ".join(json.loads(m.aliases or "[]"))}
        for m in members
    ]
    return render(
        request,
        "members.html",
        {"members": rows, "message": request.query_params.get("message")},
    )


@router.post("/members/add")
def add_member(
    request: Request,
    name: str = Form(...),
    aliases: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse("/members?message=Session+expired", status_code=303)
    if name.strip():
        session.add(
            Member(name=name.strip(), aliases=_parse_aliases(aliases), notes=notes.strip())
        )
        session.commit()
    return RedirectResponse("/members?message=Member+added", status_code=303)


@router.post("/members/{member_id}/update")
def update_member(
    member_id: int,
    request: Request,
    name: str = Form(...),
    aliases: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse("/members?message=Session+expired", status_code=303)
    member = session.get(Member, member_id)
    if member:
        member.name = name.strip()
        member.aliases = _parse_aliases(aliases)
        member.notes = notes.strip()
        session.add(member)
        session.commit()
    return RedirectResponse("/members?message=Saved", status_code=303)


@router.post("/members/{member_id}/delete")
def delete_member(
    member_id: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse("/members?message=Session+expired", status_code=303)
    member = session.get(Member, member_id)
    if member:
        session.delete(member)
        session.commit()
    return RedirectResponse("/members?message=Deleted", status_code=303)


@router.post("/members/import")
def import_members(
    request: Request,
    csrf_token: str = Form(""),
    file: UploadFile = File(...),
    user: AppUser = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse("/members?message=Session+expired", status_code=303)
    raw = file.file.read().decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    existing = {m.name.lower(): m for m in session.exec(select(Member)).all()}
    added = updated = 0
    for row in reader:
        name = (row.get("name") or row.get("Name") or "").strip()
        if not name:
            continue
        aliases = _parse_aliases(row.get("aliases") or row.get("Aliases") or "")
        notes = (row.get("notes") or row.get("Notes") or "").strip()
        member = existing.get(name.lower())
        if member:
            member.aliases, member.notes = aliases, notes
            updated += 1
        else:
            member = Member(name=name, aliases=aliases, notes=notes)
            existing[name.lower()] = member
            added += 1
        session.add(member)
    session.commit()
    return RedirectResponse(
        f"/members?message=Imported+{added}+added+{updated}+updated", status_code=303
    )
