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
    session: Session, period: str, llm: LLM, settings: Settings
) -> Newsletter:
    incidents = period_incidents(session, period)
    if not incidents:
        raise ValueError(f"no privacy incidents found for {period}")

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
