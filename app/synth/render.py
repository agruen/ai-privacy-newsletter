"""Render structured newsletter content to Markdown / HTML / plain text."""

from __future__ import annotations

import html as _html


def _url_for(content_item: dict, id_to_url: dict[str, str]) -> str:
    return id_to_url.get(content_item.get("incident_external_id", ""), "")


def render_markdown(content: dict, id_to_url: dict[str, str] | None = None) -> str:
    id_to_url = id_to_url or {}
    out: list[str] = []
    out.append("## Editor's note\n")
    out.append(content.get("editor_note", "").strip() + "\n")

    out.append("## Featured\n")
    for f in content.get("featured", []):
        url = _url_for(f, id_to_url)
        head = f.get("headline", "")
        out.append(f"### {head}" + (f" ([source]({url}))" if url else ""))
        out.append(f"\n**What happened.** {f.get('what_happened','')}")
        out.append(f"\n**Mechanism that failed.** {f.get('mechanism_failed','')}")
        out.append(f"\n**Regulatory regime.** {f.get('regime_applies','')}")
        out.append(f"\n**Standard-of-care debate.** {f.get('standard_of_care','')}\n")

    briefs = content.get("brief_mentions", [])
    if briefs:
        out.append("## Brief mentions\n")
        for b in briefs:
            url = _url_for(b, id_to_url)
            line = f"- {b.get('summary','')}"
            if url:
                line += f" ([source]({url}))"
            out.append(line)
        out.append("")

    reading = content.get("recommended_reading", [])
    if reading:
        out.append("## Recommended reading\n")
        for r in reading:
            title, url, note = r.get("title", ""), r.get("url", ""), r.get("note", "")
            label = f"[{title}]({url})" if url else title
            out.append(f"- {label} — {note}" if note else f"- {label}")
        out.append("")

    out.append("## What to watch next month\n")
    out.append(content.get("forward_look", "").strip() + "\n")
    return "\n".join(out).strip() + "\n"


def render_text(content: dict, id_to_url: dict[str, str] | None = None) -> str:
    """Plain text — also used for the member-mention scan."""
    id_to_url = id_to_url or {}
    out: list[str] = []
    out.append("EDITOR'S NOTE\n" + content.get("editor_note", "").strip() + "\n")
    out.append("FEATURED")
    for f in content.get("featured", []):
        out.append(f"\n{f.get('headline','')}")
        out.append(f"What happened: {f.get('what_happened','')}")
        out.append(f"Mechanism that failed: {f.get('mechanism_failed','')}")
        out.append(f"Regulatory regime: {f.get('regime_applies','')}")
        out.append(f"Standard-of-care debate: {f.get('standard_of_care','')}")
    briefs = content.get("brief_mentions", [])
    if briefs:
        out.append("\nBRIEF MENTIONS")
        for b in briefs:
            out.append(f"- {b.get('summary','')}")
    reading = content.get("recommended_reading", [])
    if reading:
        out.append("\nRECOMMENDED READING")
        for r in reading:
            out.append(f"- {r.get('title','')} — {r.get('note','')}")
    out.append("\nWHAT TO WATCH NEXT MONTH\n" + content.get("forward_look", "").strip())
    return "\n".join(out).strip() + "\n"


def render_html(content: dict, id_to_url: dict[str, str] | None = None) -> str:
    id_to_url = id_to_url or {}

    def esc(s: str) -> str:
        return _html.escape(s or "")

    parts: list[str] = ['<div style="font-family:Georgia,serif;max-width:680px">']
    parts.append("<h2>Editor's note</h2>")
    parts.append(f"<p>{esc(content.get('editor_note',''))}</p>")

    parts.append("<h2>Featured</h2>")
    for f in content.get("featured", []):
        url = _url_for(f, id_to_url)
        head = esc(f.get("headline", ""))
        if url:
            head = f'{head} (<a href="{esc(url)}">source</a>)'
        parts.append(f"<h3>{head}</h3>")
        parts.append(f"<p><strong>What happened.</strong> {esc(f.get('what_happened',''))}</p>")
        parts.append(f"<p><strong>Mechanism that failed.</strong> {esc(f.get('mechanism_failed',''))}</p>")
        parts.append(f"<p><strong>Regulatory regime.</strong> {esc(f.get('regime_applies',''))}</p>")
        parts.append(f"<p><strong>Standard-of-care debate.</strong> {esc(f.get('standard_of_care',''))}</p>")

    briefs = content.get("brief_mentions", [])
    if briefs:
        parts.append("<h2>Brief mentions</h2><ul>")
        for b in briefs:
            url = _url_for(b, id_to_url)
            line = esc(b.get("summary", ""))
            if url:
                line += f' (<a href="{esc(url)}">source</a>)'
            parts.append(f"<li>{line}</li>")
        parts.append("</ul>")

    reading = content.get("recommended_reading", [])
    if reading:
        parts.append("<h2>Recommended reading</h2><ul>")
        for r in reading:
            title, url, note = esc(r.get("title", "")), esc(r.get("url", "")), esc(r.get("note", ""))
            label = f'<a href="{url}">{title}</a>' if url else title
            parts.append(f"<li>{label}{' — ' + note if note else ''}</li>")
        parts.append("</ul>")

    parts.append("<h2>What to watch next month</h2>")
    parts.append(f"<p>{esc(content.get('forward_look',''))}</p>")
    parts.append("</div>")
    return "\n".join(parts)
