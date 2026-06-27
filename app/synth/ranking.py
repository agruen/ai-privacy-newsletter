"""Rank and select the month's privacy incidents for synthesis."""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.models import Incident

# Category weights — strong privacy signals rank above security-only ones.
CATEGORY_WEIGHT = {
    "pii_leakage": 3.0,
    "inferential_disclosure": 3.0,
    "regurgitation": 3.0,
    "contextual_integrity": 2.5,
    "agentic_exfiltration": 2.5,
    "dsr_failure": 2.0,
    "vendor_breach": 2.0,
    "security_vuln": 1.0,
    "other": 1.5,
}

# How strongly the LLM screen's salience boosts an incident. This lets incidents
# the source never tagged (so no AIID category) still rank by their privacy
# relevance, instead of all defaulting to the category floor.
SALIENCE_WEIGHT = {"high": 3.0, "medium": 1.5, "low": 0.5}


@dataclass
class Ranked:
    incident: Incident
    score: float


def score_incident(incident: Incident) -> float:
    score = 0.0
    cats = json.loads(incident.categories or "[]")
    score += max((CATEGORY_WEIGHT.get(c, 1.0) for c in cats), default=1.0)

    # LLM screen salience (added on top of any AIID category signal).
    score += SALIENCE_WEIGHT.get((incident.llm_salience or "").lower(), 0.0)

    payload = json.loads(incident.raw_payload or "{}")
    reports = (payload.get("reports") or "").strip()
    if reports:
        n = len([r for r in reports.replace(";", " ").split() if r])
        score += min(n, 6) * 0.4  # corroboration across sources
    if (payload.get("harmed_parties") or "").strip():
        score += 1.0
    return round(score, 3)


def rank(incidents: list[Incident]) -> list[Ranked]:
    ranked = [Ranked(i, score_incident(i)) for i in incidents]
    # Tie-break by recency (date desc) then id desc for determinism.
    ranked.sort(key=lambda r: (r.score, r.incident.incident_date, r.incident.id or 0), reverse=True)
    return ranked


def select(
    ranked: list[Ranked], featured_count: int, brief_count: int
) -> tuple[list[Ranked], list[Ranked], list[Ranked]]:
    """Split ranked incidents into (featured, brief, pool)."""
    featured = ranked[:featured_count]
    brief = ranked[featured_count : featured_count + brief_count]
    pool = ranked[featured_count + brief_count :]
    return featured, brief, pool
