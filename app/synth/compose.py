"""Orchestrate monthly newsletter synthesis."""

from __future__ import annotations

import json
import logging

from sqlmodel import Session, select

from app.config import Settings
from app.llm import LLM, BudgetExceeded, LLMError, month_spend, record_usage
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
from app.synth.render import build_context, render_text
from app.synth.screen import screen_incidents

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


def month_incidents(session: Session, period: str) -> list[Incident]:
    """Every incident dated in the month, regardless of privacy status — the
    screening pass decides which have a privacy angle."""
    return session.exec(
        select(Incident).where(Incident.incident_date.startswith(period))
    ).all()


def privacy_candidates(incidents: list[Incident]) -> list[Incident]:
    """Incidents eligible for the newsletter: AIID-tagged privacy OR LLM-flagged."""
    return [i for i in incidents if i.is_privacy or i.llm_privacy_angle]


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
    rescreen: bool = False,
) -> Newsletter:
    incidents = month_incidents(session, period)
    if not incidents:
        raise ValueError(f"no incidents found for {period}")

    # Idempotency: refuse to bill a second draft for a month that already has one
    # unless the operator explicitly asked to regenerate. Checked before any spend
    # (screening or synthesis).
    existing = latest_for_period(session, period)
    if existing and not regenerate:
        raise NewsletterExists(period, existing.id)

    # --- screen every incident in the month for a privacy angle ----------
    # Cached on the incident, so a plain regenerate reuses prior judgments and
    # only re-screens incidents that are new (or all of them when rescreen=True).
    _ensure_budget(session, settings)
    screen_incidents(session, incidents, llm, settings, rescreen=rescreen)

    candidates = privacy_candidates(incidents)
    if not candidates:
        raise ValueError(f"no privacy-angle incidents found for {period}")

    ranked = rank(candidates)
    rows, pool = select_items(ranked, settings.table_rows)

    # --- draft the issue -------------------------------------------------
    _ensure_budget(session, settings)
    try:
        content, usage = llm.complete_json(
            system=prompts.build_system(_style_guide(session)),
            user=prompts.build_user(period, rows),
            schema=prompts.DIGEST_TABLE_SCHEMA,
            model=settings.anthropic_model,
            effort=settings.synth_effort,
            max_tokens=settings.synth_max_tokens,
        )
    except LLMError as exc:
        # The call may have billed tokens before the response failed to parse;
        # record that spend so the monthly budget stays accurate, then surface
        # the failure (the scheduler/route layer records it as a failed run).
        if exc.usage is not None:
            record_usage(
                session, purpose="synthesis", model=settings.anthropic_model,
                usage=exc.usage,
            )
        raise

    newsletter = Newsletter(
        period=period,
        status="draft",
        content_json=json.dumps(content),
        note=f"{len(rows)} rows, {len(pool)} pooled",
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

    for role, group in (("row", rows), ("pool", pool)):
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
    context = build_context(
        [r.incident for r in rows], settings.row_sources_max
    )
    draft_text = render_text(content, context)
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
        except LLMError as exc:
            # A bad confirm response must not discard the already-saved draft.
            if exc.usage is not None:
                record_usage(
                    session, purpose="member_confirm",
                    model=settings.anthropic_confirm_model, usage=exc.usage,
                    newsletter_id=newsletter.id,
                )
            logger.warning(
                "member-confirm LLM error (%s) — storing flags unconfirmed", exc
            )

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
