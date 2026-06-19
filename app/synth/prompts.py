"""Prompt templates and JSON schemas for synthesis."""

from __future__ import annotations

import json

from app.models import Incident
from app.synth.ranking import Ranked

# Default FPF house-style guidance. Editable via the "style_guide" Setting.
DEFAULT_STYLE_GUIDE = """\
You write the Future of Privacy Forum (FPF) monthly AI privacy incident digest.
Voice: measured, precise, policy-literate, non-sensational. Explain the privacy
mechanism that failed and the regulatory regime in play (GDPR, CCPA/CPRA, FTC Act,
sectoral rules) without overstating legal conclusions. Be even-handed about named
companies; describe allegations as alleged. Prefer concrete detail over adjectives.
"""

NEWSLETTER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "editor_note": {"type": "string"},
        "featured": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "incident_external_id": {"type": "string"},
                    "headline": {"type": "string"},
                    "what_happened": {"type": "string"},
                    "mechanism_failed": {"type": "string"},
                    "regime_applies": {"type": "string"},
                    "standard_of_care": {"type": "string"},
                },
                "required": [
                    "incident_external_id", "headline", "what_happened",
                    "mechanism_failed", "regime_applies", "standard_of_care",
                ],
            },
        },
        "brief_mentions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "incident_external_id": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["incident_external_id", "summary"],
            },
        },
        "recommended_reading": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["title", "note"],
            },
        },
        "forward_look": {"type": "string"},
    },
    "required": [
        "editor_note", "featured", "brief_mentions",
        "recommended_reading", "forward_look",
    ],
}

MEMBER_CONFIRM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "member": {"type": "string"},
                    "is_about_member": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["member", "is_about_member", "note"],
            },
        }
    },
    "required": ["results"],
}


def _incident_brief(r: Ranked) -> dict:
    inc = r.incident
    payload = json.loads(inc.raw_payload or "{}")
    return {
        "external_id": inc.external_id,
        "date": inc.incident_date,
        "title": inc.title,
        "description": inc.description,
        "url": inc.url,
        "categories": json.loads(inc.categories or "[]"),
        "deployer": payload.get("deployer", ""),
        "developer": payload.get("developer", ""),
        "rank_score": r.score,
    }


def build_system(style_guide: str | None) -> str:
    return (style_guide or DEFAULT_STYLE_GUIDE).strip()


def build_user(period: str, featured: list[Ranked], brief: list[Ranked]) -> str:
    return (
        f"Draft the FPF AI privacy incident digest for {period}.\n\n"
        "Use ONLY the incidents below. For each featured story, set "
        "incident_external_id to the incident's external_id exactly.\n\n"
        "Write: an editor's note framing the month; "
        f"{len(featured)} featured stories (headline + what happened + which "
        "mechanism failed + which regulatory regime applies + the standard-of-care "
        "debate); brief mentions for the remaining incidents; a short recommended "
        "reading list (you may cite the incident source pages); and a forward-look "
        "at what to watch next month.\n\n"
        "FEATURED CANDIDATES:\n"
        f"{json.dumps([_incident_brief(r) for r in featured], indent=2)}\n\n"
        "BRIEF-MENTION CANDIDATES:\n"
        f"{json.dumps([_incident_brief(r) for r in brief], indent=2)}\n"
    )


def build_member_confirm_user(draft_text: str, matches: list[dict]) -> str:
    return (
        "An FPF member-watch list flagged possible mentions in this newsletter "
        "draft. For each flagged member, decide whether the draft text is genuinely "
        "ABOUT that organization (true) or just an incidental/unrelated mention "
        "(false), and give a one-line note.\n\n"
        f"FLAGGED MEMBERS:\n{json.dumps(matches, indent=2)}\n\n"
        f"DRAFT TEXT:\n{draft_text}\n"
    )
