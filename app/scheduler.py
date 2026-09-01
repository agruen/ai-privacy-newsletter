"""APScheduler setup: daily ingestion and monthly synthesis jobs.

Phase 0 wires the scheduler with placeholder jobs that log a Run row. The real
ingest/synthesis logic is implemented in later phases.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session

from app.config import get_settings
from app.db import engine
from app.models import Run, utcnow

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")


def record_run(job: str, status: str, detail: str = "") -> None:
    with Session(engine) as session:
        run = Run(job=job, status=status, detail=detail, finished_at=utcnow())
        session.add(run)
        session.commit()


def daily_ingest() -> None:
    """Ingest new incidents from all enabled sources, then classify pending."""
    from app.classify.runner import classify_pending
    from app.ingest.runner import run_ingest

    with Session(engine) as session:
        summaries = run_ingest(session)
        classified = classify_pending(session)

    parts = [
        f"{s.source}: +{s.created}/{s.updated}u" + (f" ERR {s.error}" if s.error else "")
        for s in summaries
    ]
    detail = "; ".join(parts) + f"; classified {classified}"
    status = "error" if any(s.error for s in summaries) else "ok"
    record_run("daily_ingest", status, detail)


def previous_month(now=None) -> str:
    """Return the previous calendar month as 'YYYY-MM' (UTC)."""
    now = now or utcnow()
    first = now.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.strftime("%Y-%m")


def generate_for_period(
    period: str, regenerate: bool = False, rescreen: bool = False
) -> str:
    """Generate a draft for a period; returns a status detail string."""
    from app.llm import get_llm
    from app.settings_store import resolve_anthropic_key
    from app.synth.compose import generate_newsletter

    settings = get_settings()
    with Session(engine) as session:
        llm = get_llm(resolve_anthropic_key(session, settings))
        nl = generate_newsletter(
            session, period, llm, settings,
            regenerate=regenerate, rescreen=rescreen,
        )
        return f"period {period}: newsletter #{nl.id} ({nl.note})"


def monthly_synth() -> None:
    """Draft the previous month's newsletter for human review."""
    from app.synth.compose import NewsletterExists

    period = previous_month()
    try:
        detail = generate_for_period(period)
        record_run("monthly_synth", "ok", detail)
    except NewsletterExists:
        # A draft already exists (e.g. generated manually, or a coalesced refire).
        # Not an error — skip without re-billing.
        record_run("monthly_synth", "ok", f"{period}: draft already exists, skipped")
    except Exception as exc:  # surfaced on the dashboard
        logger.exception("monthly_synth failed")
        record_run("monthly_synth", "error", f"{period}: {exc}")


def email_poll() -> None:
    """Check the intake mailbox for new mail (deterministic UID cursor)."""
    from app.mail.poll import poll_inbox

    try:
        with Session(engine) as session:
            poll_inbox(session, get_settings())
    except Exception as exc:  # e.g. IMAP down — surfaced on the dashboard
        logger.exception("email_poll failed")
        record_run("email_poll", "error", str(exc))


def aiid_check() -> None:
    """Daily AIID check: ingest, screen what's new, email the digest."""
    from app.mail.aiid_check import run_aiid_check

    try:
        with Session(engine) as session:
            detail = run_aiid_check(session, get_settings())
        record_run("aiid_check", "ok", detail)
    except Exception as exc:
        logger.exception("aiid_check failed")
        record_run("aiid_check", "error", str(exc))


def start_scheduler() -> None:
    settings = get_settings()
    email_on = settings.email_configured
    if not (settings.scheduler_enabled or email_on):
        logger.info("scheduler disabled via settings")
        return
    if scheduler.running:
        return

    if settings.scheduler_enabled:
        scheduler.add_job(
            daily_ingest,
            CronTrigger.from_crontab(settings.daily_ingest_cron, timezone="UTC"),
            id="daily_ingest",
            replace_existing=True,
        )
        scheduler.add_job(
            monthly_synth,
            CronTrigger.from_crontab(settings.monthly_synth_cron, timezone="UTC"),
            id="monthly_synth",
            replace_existing=True,
        )
    if email_on:
        scheduler.add_job(
            email_poll,
            IntervalTrigger(minutes=settings.email_poll_minutes),
            id="email_poll",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
            aiid_check,
            # Local-time cron (e.g. noon America/New_York), DST handled by the
            # trigger — no UTC conversion to maintain by hand.
            CronTrigger.from_crontab(
                settings.aiid_check_cron, timezone=settings.aiid_check_tz
            ),
            id="aiid_check",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
    scheduler.start()
    logger.info(
        "scheduler started (cron jobs: %s, email channel: %s)",
        settings.scheduler_enabled,
        email_on,
    )


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
