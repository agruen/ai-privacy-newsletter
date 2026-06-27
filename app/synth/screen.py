"""LLM privacy screening: judge a privacy angle for every incident in a month.

This is the front half of generation. Where the AIID tag classifier (ingest time)
only marks incidents the source itself tagged "Privacy & Security", this pass asks
the model about *every* incident in the month, so the newsletter can surface
privacy angles the source never tagged. Judgments are persisted on the incident
and reused on later runs unless a re-screen is requested, so a regenerate does not
re-bill the screen.
"""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.config import Settings
from app.llm import LLM, LLMError, record_usage
from app.models import Incident, utcnow
from app.synth import prompts

logger = logging.getLogger(__name__)

_VALID_SALIENCE = {"high", "medium", "low"}


def screen_incidents(
    session: Session,
    incidents: list[Incident],
    llm: LLM,
    settings: Settings,
    *,
    rescreen: bool = False,
) -> int:
    """Screen incidents for a privacy angle; persist results. Returns how many
    incidents were sent to the model this call (0 if all were already cached)."""
    todo = incidents if rescreen else [i for i in incidents if not i.llm_screened]
    if not todo:
        return 0

    model = settings.resolved_screen_model
    try:
        result, usage = llm.complete_json(
            system="You are a careful privacy analyst screening AI incidents.",
            user=prompts.build_screen_user(todo),
            schema=prompts.SCREEN_SCHEMA,
            model=model,
            effort=settings.screen_effort,
            max_tokens=settings.screen_max_tokens,
        )
    except LLMError as exc:
        # Record any spend incurred before the failure, then re-raise so the
        # caller surfaces it (same contract as synthesis).
        if exc.usage is not None:
            record_usage(session, purpose="screen", model=model, usage=exc.usage)
        raise

    record_usage(session, purpose="screen", model=model, usage=usage)

    by_id = {i.external_id: i for i in todo}
    now = utcnow()
    assessed = 0
    for a in result.get("assessments", []):
        inc = by_id.get(a.get("incident_external_id", ""))
        if inc is None:
            continue
        has_angle = bool(a.get("has_privacy_angle"))
        salience = (a.get("salience") or "").strip().lower()
        inc.llm_screened = True
        inc.llm_privacy_angle = has_angle
        inc.llm_salience = salience if (has_angle and salience in _VALID_SALIENCE) else ""
        inc.llm_privacy_note = (a.get("angle") or "").strip() if has_angle else ""
        inc.llm_screened_at = now
        inc.llm_screen_model = model
        session.add(inc)
        assessed += 1

    # Any incident the model omitted is still marked screened (negative) so a
    # rerun doesn't keep paying to re-screen it.
    for inc in todo:
        if not inc.llm_screened:
            inc.llm_screened = True
            inc.llm_privacy_angle = False
            inc.llm_screened_at = now
            inc.llm_screen_model = model
            session.add(inc)

    session.commit()
    flagged = sum(1 for i in todo if i.llm_privacy_angle)
    logger.info("screened %d incidents, %d with a privacy angle", len(todo), flagged)
    return len(todo)
