"""Orchestrate monthly newsletter synthesis."""

from __future__ import annotations

import json
import logging

from sqlmodel import Session, select

from app.config import Settings
from app.llm import LLM, BudgetExceeded, month_spend, record_usage
from app.matching import find_member_mentions
from app.models import (
    Incident,
    Member,
    MemberFlag,
    Newsletter,
    NewsletterItem,
    Setting,
    utcnow,
)
from app.synth import prompts
from app.synth.ranking import rank, select as select_items
from app.synth.render import render_text

logger = logging.getLogger(__name__)


class NewsletterExists(Exception):
    """Raised when a draft already exists for the period and regenerate is off.

    Generation is the only paid LLM path, and it is triggered by a button, so a
    repeat click (or generating a month that already has a draft) must not silently
    bill a second Opus call. Callers either show this to the operator or pass
    ``regenerate=True`` to deliberately replace the existing draft.
    """

    def __init__(self, period: str, newsletter_id: int | None) -> None:
        self.period = period
        self.newsletter_id = newsletter_id
        super().__init__(f"a draft for {period} already exists")


def latest_for_period(session: Session, period: str) -> Newsletter | None:
    return session.exec(
        select(Newsletter)
        .where(Newsletter.period == period)
        .order_by(Newsletter.created_at.desc())
    ).first()


def _replace_prior_drafts(session: Session, period: str, *, keep_id: int) -> None:
    """Delete other drafts for the period and their child rows on regenerate.

    LLMUsage rows are intentionally kept: the money was spent regardless of which
    draft we retain, so month spend must still reflect it.
    """
    others = session.exec(
        select(Newsletter)
        .where(Newsletter.period == period)
        .where(Newsletter.id != keep_id)
    ).all()
    for old in others:
        for item in session.exec(
            select(NewsletterItem).where(NewsletterItem.newsletter_id == old.id)
        ).all():
            session.delete(item)
        for flag in session.exec(
            select(MemberFlag).where(MemberFlag.newsletter_id == old.id)
        ).all():
            session.delete(flag)
        session.delete(old)
    session.commit()


def period_incidents(session: Session, period: str) -> list[Incident]:
    return session.exec(
        select(Incident)
        .where(Incident.is_privacy == True)  # noqa: E712
        .where(Incident.incident_date.startswith(period))
    ).all()


def _style_guide(session: Session) -> str | None:
    s = session.get(Setting, "style_guide")
    return s.value if s and s.value.strip() else None


def _ensure_budget(session: Session, settings: Settings) -> None:
    if month_spend(session) >= settings.anthropic_monthly_budget_usd:
        raise BudgetExceeded(
            f"monthly LLM budget ${settings.anthropic_monthly_budget_usd:.0f} reached"
        )


def generate_newsletter(
    session: Session,
    period: str,
    llm: LLM,
    settings: Settings,
    *,
    regenerate: bool = False,
) -> Newsletter:
    incidents = period_incidents(session, period)
    if not incidents:
        raise ValueError(f"no privacy incidents found for {period}")

    # Idempotency: refuse to bill a second draft for a month that already has one
    # unless the operator explicitly asked to regenerate. Checked before any spend.
    existing = latest_for_period(session, period)
    if existing and not regenerate:
        raise NewsletterExists(period, existing.id)

    ranked = rank(incidents)
    featured, brief, pool = select_items(
        ranked, settings.featured_count, settings.brief_count
    )

    # --- draft the issue -------------------------------------------------
    _ensure_budget(session, settings)
    content, usage = llm.complete_json(
        system=prompts.build_system(_style_guide(session)),
        user=prompts.build_user(period, featured, brief),
        schema=prompts.NEWSLETTER_SCHEMA,
        model=settings.anthropic_model,
        effort=settings.synth_effort,
    )

    newsletter = Newsletter(
        period=period,
        status="draft",
        content_json=json.dumps(content),
        note=f"{len(featured)} featured, {len(brief)} brief, {len(pool)} pooled",
    )
    session.add(newsletter)
    session.commit()
    session.refresh(newsletter)
    record_usage(
        session, purpose="synthesis", model=settings.anthropic_model,
        usage=usage, newsletter_id=newsletter.id,
    )

    # On regenerate, drop the prior draft(s) now that the replacement is committed.
    if regenerate:
        _replace_prior_drafts(session, period, keep_id=newsletter.id)

    for role, group in (("featured", featured), ("brief", brief), ("pool", pool)):
        for i, r in enumerate(group):
            session.add(
                NewsletterItem(
                    newsletter_id=newsletter.id,
                    incident_id=r.incident.id,
                    role=role,
                    rank=i,
                    score=r.score,
                )
            )
    session.commit()

    # --- member-flag pass ------------------------------------------------
    id_to_url = {r.incident.external_id: r.incident.url for r in featured + brief}
    draft_text = render_text(content, id_to_url)
    members = session.exec(select(Member)).all()
    matches = find_member_mentions(draft_text, members)

    confirmations: dict[str, tuple[bool, str]] = {}
    if matches:
        try:
            _ensure_budget(session, settings)
            payload = [
                {"member": m.member.name, "snippet": m.snippet} for m in matches
            ]
            result, c_usage = llm.complete_json(
                system="You verify whether a newsletter is about specific organizations.",
                user=prompts.build_member_confirm_user(draft_text, payload),
                schema=prompts.MEMBER_CONFIRM_SCHEMA,
                model=settings.anthropic_confirm_model,
                effort="low",
            )
            record_usage(
                session, purpose="member_confirm",
                model=settings.anthropic_confirm_model, usage=c_usage,
                newsletter_id=newsletter.id,
            )
            for r in result.get("results", []):
                confirmations[r.get("member", "")] = (
                    bool(r.get("is_about_member")), r.get("note", ""),
                )
        except BudgetExceeded:
            logger.warning("budget exceeded — storing member flags unconfirmed")

    for m in matches:
        confirmed, note = confirmations.get(m.member.name, (None, "not confirmed"))
        session.add(
            MemberFlag(
                newsletter_id=newsletter.id,
                member_id=m.member.id,
                member_name=m.member.name,
                term=m.term,
                snippet=m.snippet,
                confirmed=confirmed,
                note=note,
            )
        )
    session.commit()
    session.refresh(newsletter)
    return newsletter
