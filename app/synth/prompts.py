"""Prompt templates and JSON schemas for synthesis."""

from __future__ import annotations

import json

from app.models import Incident
from app.synth.ranking import Ranked

# Default FPF house-style guidance. Editable via the "style_guide" Setting.
DEFAULT_STYLE_GUIDE = """\
You write the Future of Privacy Forum (FPF) monthly AI privacy incident digest.

AUDIENCE: in-house privacy professionals — privacy counsel, chief privacy
officers, DPOs, and privacy program managers — working inside large, mostly
U.S.-headquartered corporations (FPF member companies across technology, finance,
healthcare, retail, automotive, telecom, ad-tech, pharma, and more). They are
sophisticated privacy practitioners, but do NOT assume they track international
developments closely. Write for a reader deciding what an incident means for
their own company's privacy program — not for an academic or policy-wonk audience.

ORIENTATION:
- Default to a U.S. corporate frame. When you reference a non-U.S. law, regulator,
  or framework (e.g. the EU's GDPR, the UK GDPR, the EU AI Act, a national data
  protection authority, or a specific country's statute), briefly say what it is
  and why it matters to a U.S.-based company — do not assume the reader already
  knows international regimes. Expand acronyms on first use.
- Make each story actionable: the risk column names the failure pattern and the
  control or governance gap behind it. That is the whole takeaway — a label and
  one short sentence, not a paragraph of program advice, and not legal advice.

VOICE: measured, precise, policy-literate, non-sensational — and above all
BRIEF. The digest is a scannable table, not an essay; a reader should absorb a
row in seconds. Explain the privacy
mechanism that failed and the regulatory regime in play — U.S. first (FTC Act,
state laws such as CCPA/CPRA, sectoral rules such as HIPAA/GLBA/COPPA), then any
relevant non-U.S. regime, explained — without overstating legal conclusions. Be
even-handed about named companies and describe allegations as alleged. Prefer
concrete detail over adjectives. A reader's own employer may appear in these
stories, so be accurate and fair.
"""

# The issue is a table: one row per incident. Date, incident number, the AI
# Incident Database link, and the source links are joined from our own data at
# render time — never asked of the model, which would invent URLs.
DIGEST_TABLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "incident_external_id": {"type": "string"},
                    "headline": {"type": "string"},
                    "what_happened": {"type": "string"},
                    "risk_category": {"type": "string"},
                    "risk_explanation": {"type": "string"},
                },
                "required": [
                    "incident_external_id", "headline", "what_happened",
                    "risk_category", "risk_explanation",
                ],
            },
        },
    },
    "required": ["rows"],
}

SCREEN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "incident_external_id": {"type": "string"},
                    "has_privacy_angle": {"type": "boolean"},
                    "angle": {"type": "string"},
                    "salience": {
                        "type": "string",
                        "enum": ["high", "medium", "low", "none"],
                    },
                },
                "required": [
                    "incident_external_id", "has_privacy_angle", "angle", "salience",
                ],
            },
        }
    },
    "required": ["assessments"],
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
    links = payload.get("report_links") or []
    return {
        "external_id": inc.external_id,
        "date": inc.incident_date,
        "title": inc.title,
        "description": inc.description,
        "categories": json.loads(inc.categories or "[]"),
        "deployer": payload.get("deployer", ""),
        "developer": payload.get("developer", ""),
        # Publications only, never their URLs: the model should know how well
        # corroborated an incident is without being handed links it might echo
        # into prose. The renderer attaches the real links afterwards.
        "reported_by": [
            d for d in
            ((link.get("source_domain") or "").strip() for link in links) if d
        ],
        "report_headlines": [
            (link.get("title") or "").strip() for link in links[:3]
            if (link.get("title") or "").strip()
        ],
        # The privacy angle the screening pass identified (may be empty for
        # incidents included only via the AIID tag).
        "privacy_angle": inc.llm_privacy_note,
        "salience": inc.llm_salience,
        "rank_score": r.score,
    }


def build_system(style_guide: str | None) -> str:
    return (style_guide or DEFAULT_STYLE_GUIDE).strip()


def build_user(period: str, rows: list[Ranked]) -> str:
    return (
        f"Build the FPF AI privacy incident table for {period}.\n\n"
        "The deliverable is a TABLE, not a written newsletter: one row per "
        "incident, no editor's note, no framing prose, no closing section. "
        "Use ONLY the incidents below, one row each, in the order given, and "
        "echo incident_external_id exactly so the row can be matched back.\n\n"
        "Each row has four written fields. BE TERSE: every cell renders in a "
        "narrow table column, so cut every word that is not load-bearing. The "
        "word caps below are hard limits, not targets to fill.\n"
        "- headline: a specific, factual headline in title case, 10 words or "
        "fewer. Name the actor and the failure. No trailing period.\n"
        "- what_happened: one or two short sentences, 35 words TOTAL at most — "
        "who did what to whose data, and the outcome. Skip secondary detail "
        "(case numbers, precise dollar amounts, official titles, follow-on "
        "developments); the linked sources carry it. Describe unproven claims "
        "as alleged, but one 'alleged'/'reportedly' per row is enough. Do NOT "
        "write 'Source:' and do NOT include any URLs or citations: the source "
        "links are added automatically from our own records, and a URL you "
        "write from memory would be wrong.\n"
        "- risk_category: a short governance-risk label in title case, 2-5 "
        "words, naming the failure pattern a privacy team would recognize "
        "(for example 'Shadow AI & Supply Chain Vulnerability', 'Consent "
        "Bypass & Moderation Failure', 'Confused Deputy Scenario'). Reuse the "
        "same label across rows when the pattern is genuinely the same.\n"
        "- risk_explanation: one short sentence, 18 words or fewer, naming the "
        "specific control or governance gap that let it happen. State the gap "
        "directly — no 'teams should verify that…' framing, no hedging filler, "
        "no advice boilerplate.\n\n"
        "Keep every field to a single paragraph with no line breaks, no bullet "
        "lists, and no pipe characters: these render inside table cells.\n\n"
        "INCIDENTS:\n"
        f"{json.dumps([_incident_brief(r) for r in rows], indent=2)}\n"
    )


def _screen_brief(inc: Incident) -> dict:
    payload = json.loads(inc.raw_payload or "{}")
    return {
        "external_id": inc.external_id,
        "date": inc.incident_date,
        "title": inc.title,
        "description": inc.description,
        "deployer": payload.get("deployer", ""),
        "developer": payload.get("developer", ""),
        "harmed_parties": payload.get("harmed_parties", ""),
        # The AIID privacy tag is a hint, not the gate — the LLM decides.
        "aiid_tagged_privacy": bool(inc.is_privacy),
    }


def build_screen_user(incidents: list[Incident]) -> str:
    return (
        "You are screening AI incidents for a privacy newsletter aimed at in-house "
        "privacy professionals at large U.S. companies. For EVERY incident below, "
        "decide whether it has an interesting privacy angle worth covering.\n\n"
        "Use a BROAD bar: flag anything with a plausible personal-data, surveillance, "
        "biometric, profiling, tracking, re-identification, data-sharing, consent, or "
        "data-governance dimension — even if that angle is secondary to the main "
        "story. The source's own privacy tag is only a hint (aiid_tagged_privacy); "
        "judge the incident yourself.\n\n"
        "Return exactly one assessment per incident, echoing incident_external_id "
        "verbatim. Set has_privacy_angle true/false; when true, give a one-sentence "
        "'angle' naming the specific privacy dimension a privacy team would care "
        "about, and a 'salience' of how newsletter-worthy that angle is (high / "
        "medium / low). When false, set salience to 'none' and angle to ''.\n\n"
        "INCIDENTS:\n"
        f"{json.dumps([_screen_brief(i) for i in incidents], indent=2)}\n"
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
