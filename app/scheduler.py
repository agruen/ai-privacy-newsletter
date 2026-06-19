"""APScheduler setup: daily ingestion and monthly synthesis jobs.

Phase 0 wires the scheduler with placeholder jobs that log a Run row. The real
ingest/synthesis logic is implemented in later phases.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session

from app.config import get_settings
from app.db import engine
from app.models import Run, utcnow

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")


def _record_run(job: str, status: str, detail: str = "") -> None:
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
    _record_run("daily_ingest", status, detail)


def previous_month(now=None) -> str:
    """Return the previous calendar month as 'YYYY-MM' (UTC)."""
    now = now or utcnow()
    first = now.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.strftime("%Y-%m")


def generate_for_period(period: str) -> str:
    """Generate a draft for a period; returns a status detail string."""
    from app.llm import get_llm
    from app.synth.compose import generate_newsletter

    settings = get_settings()
    with Session(engine) as session:
        llm = get_llm(settings.anthropic_api_key)
        nl = generate_newsletter(session, period, llm, settings)
        return f"period {period}: newsletter #{nl.id} ({nl.note})"


def monthly_synth() -> None:
    """Draft the previous month's newsletter for human review."""
    period = previous_month()
    try:
        detail = generate_for_period(period)
        _record_run("monthly_synth", "ok", detail)
    except Exception as exc:  # surfaced on the dashboard
        logger.exception("monthly_synth failed")
        _record_run("monthly_synth", "error", f"{period}: {exc}")


def start_scheduler() -> None:
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("scheduler disabled via settings")
        return
    if scheduler.running:
        return

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
    scheduler.start()
    logger.info("scheduler started")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
