"""Member name/alias matching against free text.

Used by the monthly synthesis (Phase 5) to flag drafts that mention an FPF
member. Matching is a fast first pass (word-boundary, case-insensitive); an LLM
confirmation step decides whether the mention is genuinely about that member.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.models import Member


def member_terms(member: Member) -> list[str]:
    """All names to search for: the canonical name plus any aliases."""
    terms = [member.name.strip()]
    try:
        terms += [a for a in json.loads(member.aliases or "[]") if a and a.strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    # De-duplicate while preserving order, drop very short terms.
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        t = t.strip()
        key = t.lower()
        if len(t) >= 3 and key not in seen:
            seen.add(key)
            out.append(t)
    return out


@dataclass
class MemberMatch:
    member: Member
    term: str
    snippet: str


def _snippet(text: str, start: int, end: int, radius: int = 80) -> str:
    a = max(0, start - radius)
    b = min(len(text), end + radius)
    prefix = "…" if a > 0 else ""
    suffix = "…" if b < len(text) else ""
    return f"{prefix}{text[a:b].strip()}{suffix}"


def find_member_mentions(text: str, members: list[Member]) -> list[MemberMatch]:
    """Return one match per member that appears in ``text`` (first hit only)."""
    if not text:
        return []
    matches: list[MemberMatch] = []
    for member in members:
        for term in member_terms(member):
            pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
            m = pattern.search(text)
            if m:
                matches.append(
                    MemberMatch(member=member, term=term,
                                snippet=_snippet(text, m.start(), m.end()))
                )
                break  # one match per member is enough to flag
    return matches
