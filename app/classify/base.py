"""Classifier interface for deciding privacy relevance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Result statuses stored on Incident.status.
STATUS_PENDING = "pending"          # not yet decidable (e.g. no source tags yet)
STATUS_CLASSIFIED = "classified"    # privacy-relevant
STATUS_NOT_PRIVACY = "not_privacy"  # decided, not privacy-relevant
STATUS_NEEDS_REVIEW = "needs_review"  # low-confidence; surface in monthly run


@dataclass
class Classification:
    status: str
    is_privacy: bool = False
    categories: list[str] = field(default_factory=list)
    confidence: float = 0.0
    decided_by: str = ""


@runtime_checkable
class Classifier(Protocol):
    name: str

    def classify(self, payload: dict[str, Any]) -> Classification:
        """Classify a single incident from its source payload."""
        ...
