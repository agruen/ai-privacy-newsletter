"""Render a digest issue as a table — Markdown, HTML, or plain text.

The issue is one row per incident. Only four cells are written by the model
(headline, what happened, risk category, risk explanation); the date, the
incident number, the AI Incident Database link, and the source links are joined
here from the incident rows we ingested. Links never come from the model: it
would produce plausible URLs that do not resolve.
"""

from __future__ import annotations

import html as _html
import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

COLUMNS = (
    "Date",
    "Incident Number",
    "Headline",
    "What Happened",
    "AI Governance Risk Categories",
)


def _safe_url(url: str) -> str:
    """Return the URL only if it uses http(s); else "" so no link is emitted.

    Content URLs come from ingested data and end up in anchor hrefs in the
    exported HTML, so reject javascript:/data: and other schemes.
    """
    u = (url or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else ""


@dataclass
class Source:
    """One citable publication behind an incident."""

    label: str          # what the link reads as, e.g. "theverge.com"
    url: str
    title: str = ""     # the article headline, used as a tooltip in HTML


@dataclass
class RowContext:
    """Everything about a row that comes from our data, not from the model."""

    date: str = ""                  # ISO date as ingested
    number: str = ""                # AIID incident number ("" when not from AIID)
    url: str = ""                   # AI Incident Database entry for the headline
    sources: list[Source] = field(default_factory=list)


def _domain_label(url: str, source_domain: str = "") -> str:
    domain = (source_domain or "").strip().lower()
    if not domain:
        domain = urlsplit(url).netloc.lower()
    return domain.removeprefix("www.")


def _incident_sources(incident, max_sources: int) -> list[Source]:
    """The public links we hold for an incident, best first.

    AIID incidents carry resolved ``report_links`` from the snapshot. Incidents
    submitted by email instead carry whatever the write-up's research step
    corroborated, falling back to the URLs the submitter themselves cited.
    """
    payload = json.loads(incident.raw_payload or "{}")
    candidates: list[tuple[str, str, str]] = [  # (url, source_domain, title)
        (link.get("url", ""), link.get("source_domain", ""), link.get("title", ""))
        for link in payload.get("report_links") or []
    ]
    if not candidates:
        writeup = json.loads(incident.writeup_json or "{}")
        candidates = [
            (s.get("url", ""), "", s.get("title", ""))
            for s in writeup.get("sources") or []
        ]
    if not candidates:
        candidates = [(u, "", "") for u in payload.get("urls") or []]

    sources: list[Source] = []
    seen: set[str] = set()
    for url, domain, title in candidates:
        safe = _safe_url(url)
        if not safe:
            continue
        label = _domain_label(safe, domain)
        if not label or label in seen:
            continue
        seen.add(label)
        sources.append(Source(label=label, url=safe, title=title))
        if len(sources) >= max_sources:
            break
    return sources


def build_context(incidents, max_sources: int = 3) -> dict[str, RowContext]:
    """Map incident external_id -> the data-derived half of its row."""
    return {
        inc.external_id: RowContext(
            date=inc.incident_date or "",
            # Only AIID incidents have a number in the database being cited;
            # an emailed submission has no public entry to point at.
            number=inc.external_id if inc.source == "aiid" else "",
            url=_safe_url(inc.url),
            sources=_incident_sources(inc, max_sources),
        )
        for inc in incidents
    }


def format_date(iso_date: str) -> str:
    """ISO 2026-05-03 -> 5/3/26, the form the FPF table uses."""
    parts = (iso_date or "").strip()[:10].split("-")
    if len(parts) != 3:
        return parts[0] if parts and parts[0] else ""
    year, month, day = parts
    try:
        return f"{int(month)}/{int(day)}/{int(year) % 100:02d}"
    except ValueError:
        return iso_date.strip()


# -- Markdown --------------------------------------------------------------

def _md_cell(text: str) -> str:
    """One table cell: pipes escaped, newlines flattened.

    A raw pipe or newline in a cell silently breaks the row into the wrong
    number of columns, which is invisible until someone pastes the table.
    """
    return " ".join((text or "").split()).replace("|", "\\|")


def _md_link_text(text: str) -> str:
    """Link text: brackets escaped so they can't close the link early."""
    return _md_cell(text).replace("[", "\\[").replace("]", "\\]")


def _md_sources(sources: list[Source]) -> str:
    if not sources:
        return ""
    links = ", ".join(f"[{_md_link_text(s.label)}]({s.url})" for s in sources)
    return f" Source: {links}"


def render_markdown(content: dict, context: dict[str, RowContext] | None = None) -> str:
    context = context or {}
    lines = [
        "| " + " | ".join(COLUMNS) + " |",
        "| " + " | ".join([":----"] * len(COLUMNS)) + " |",
    ]
    for row in content.get("rows", []):
        ctx = context.get(row.get("incident_external_id", ""), RowContext())
        headline = f"**{_md_link_text(row.get('headline', ''))}**"
        if ctx.url:
            headline = f"[{headline}]({ctx.url})"
        what = _md_cell(row.get("what_happened", "")) + _md_sources(ctx.sources)
        category = _md_cell(row.get("risk_category", ""))
        explanation = _md_cell(row.get("risk_explanation", ""))
        risk = f"**{category}:** {explanation}".strip() if category else explanation
        lines.append(
            "| "
            + " | ".join(
                [format_date(ctx.date), _md_cell(ctx.number), headline, what, risk]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


# -- Plain text ------------------------------------------------------------

def render_text(content: dict, context: dict[str, RowContext] | None = None) -> str:
    """Plain text — also what the member-mention scan reads."""
    context = context or {}
    out: list[str] = []
    for row in content.get("rows", []):
        ctx = context.get(row.get("incident_external_id", ""), RowContext())
        date = format_date(ctx.date)
        number = f" (AI Incident Database #{ctx.number})" if ctx.number else ""
        out.append(f"{date}{number}".strip())
        out.append(row.get("headline", ""))
        what = " ".join((row.get("what_happened", "") or "").split())
        if ctx.sources:
            what += " Source: " + ", ".join(f"{s.label} {s.url}" for s in ctx.sources)
        out.append(what)
        category = (row.get("risk_category", "") or "").strip()
        explanation = (row.get("risk_explanation", "") or "").strip()
        out.append(f"{category}: {explanation}" if category else explanation)
        out.append("")
    return "\n".join(out).strip() + "\n"


# -- HTML ------------------------------------------------------------------

def render_html(content: dict, context: dict[str, RowContext] | None = None) -> str:
    context = context or {}

    def esc(s: str) -> str:
        return _html.escape(" ".join((s or "").split()))

    parts = [
        '<table style="border-collapse:collapse;font-family:Georgia,serif;'
        'font-size:14px" cellpadding="8" border="1">',
        "<thead><tr>"
        + "".join(f'<th align="left">{c}</th>' for c in COLUMNS)
        + "</tr></thead>",
        "<tbody>",
    ]
    for row in content.get("rows", []):
        ctx = context.get(row.get("incident_external_id", ""), RowContext())
        headline = f"<strong>{esc(row.get('headline', ''))}</strong>"
        if ctx.url:
            headline = f'<a href="{esc(ctx.url)}">{headline}</a>'
        what = esc(row.get("what_happened", ""))
        if ctx.sources:
            links = ", ".join(
                f'<a href="{esc(s.url)}" title="{esc(s.title)}">{esc(s.label)}</a>'
                for s in ctx.sources
            )
            what += f" Source: {links}"
        category = esc(row.get("risk_category", ""))
        explanation = esc(row.get("risk_explanation", ""))
        risk = f"<strong>{category}:</strong> {explanation}" if category else explanation
        cells = [
            esc(format_date(ctx.date)), esc(ctx.number), headline, what, risk,
        ]
        parts.append(
            "<tr>" + "".join(f'<td valign="top">{c}</td>' for c in cells) + "</tr>"
        )
    parts.append("</tbody></table>")
    return "\n".join(parts)
