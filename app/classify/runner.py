"""Run classification over pending incidents."""

from __future__ import annotations

import json
import logging

from sqlmodel import Session, select

from app.classify.aiid_tags import AIIDTagClassifier
from app.classify.base import Classifier
from app.models import Incident, utcnow

logger = logging.getLogger(__name__)

# Active classifier. Swapping to an LLM/ensemble classifier happens here.
CLASSIFIERS = {
    AIIDTagClassifier.name: AIIDTagClassifier,
}
DEFAULT_CLASSIFIER = AIIDTagClassifier.name


def get_classifier(name: str | None = None) -> Classifier:
    return CLASSIFIERS[name or DEFAULT_CLASSIFIER]()


def classify_incident(incident: Incident, classifier: Classifier) -> None:
    payload = json.loads(incident.raw_payload or "{}")
    result = classifier.classify(payload)
    incident.status = result.status
    incident.is_privacy = result.is_privacy
    incident.categories = json.dumps(result.categories)
    incident.confidence = result.confidence
    incident.decided_by = result.decided_by
    incident.classified_at = utcnow()


def classify_pending(session: Session, classifier: Classifier | None = None) -> int:
    """Classify all incidents still in the 'pending' state. Returns the count
    of incidents that became a decided (non-pending) state."""
    classifier = classifier or get_classifier()
    pending = session.exec(
        select(Incident).where(Incident.status == "pending")
    ).all()
    decided = 0
    for incident in pending:
        classify_incident(incident, classifier)
        if incident.status != "pending":
            decided += 1
        session.add(incident)
    session.commit()
    logger.info("classified %d/%d pending incidents", decided, len(pending))
    return decided
