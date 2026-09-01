"""Rendering the digest table: the joined-in columns and the export formats."""

import json

from app.models import Incident
from app.synth.render import (
    build_context,
    format_date,
    render_html,
    render_markdown,
    render_text,
)


def _aiid_incident(ext="1507", date="2026-05-03", links=None):
    return Incident(
        source="aiid", external_id=ext, dedup_key=f"aiid:{ext}",
        title="t", description="d",
        url=f"https://incidentdatabase.ai/cite/{ext}", incident_date=date,
        raw_payload=json.dumps({"report_links": links if links is not None else [
            {"url": "https://www.thehindu.com/a", "source_domain": "thehindu.com",
             "title": "CBSE says portal vulnerabilities contained"},
        ]}),
    )


def _email_incident(ext="em-abc", date="2026-05-14"):
    return Incident(
        source="email", external_id=ext, dedup_key=f"email:{ext}",
        title="t", description="d", url="", incident_date=date,
        raw_payload=json.dumps({"from": "tip@example.org", "urls": []}),
        writeup_json=json.dumps({
            "sources": [{"title": "DOJ press release", "url": "https://justice.gov/x"}]
        }),
    )


def _content(ext, headline="Vendor QA Scripts Allegedly Fed Minors' Exam Data"):
    return {
        "rows": [{
            "incident_external_id": ext,
            "headline": headline,
            "what_happened": "A vendor allegedly processed student answer sheets "
                             "through a public LLM.",
            "risk_category": "Shadow AI & Supply Chain Vulnerability",
            "risk_explanation": "A third-party processor routed identifiable minor "
                                "records into a public LLM without safeguards.",
        }]
    }


def test_format_date_uses_the_house_form():
    assert format_date("2026-05-03") == "5/3/26"
    assert format_date("2026-12-25") == "12/25/26"
    assert format_date("") == ""            # an undated row renders blank
    assert format_date("not-a-date") == "not-a-date"


def test_markdown_row_has_the_five_columns_in_order():
    inc = _aiid_incident()
    md = render_markdown(_content("1507"), build_context([inc]))
    header, divider, row = md.strip().splitlines()

    assert header == (
        "| Date | Incident Number | Headline | What Happened "
        "| AI Governance Risk Categories |"
    )
    assert divider.count(":----") == 5
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[0] == "5/3/26"
    assert cells[1] == "1507"
    # Headline is bold and links to the incident database entry.
    assert cells[2] == (
        "[**Vendor QA Scripts Allegedly Fed Minors' Exam Data**]"
        "(https://incidentdatabase.ai/cite/1507)"
    )
    # The public link we hold is appended to what happened.
    assert cells[3].endswith("Source: [thehindu.com](https://www.thehindu.com/a)")
    assert cells[4].startswith("**Shadow AI & Supply Chain Vulnerability:**")


def test_email_submitted_row_has_no_number_and_no_incident_link():
    """Nothing to cite in the incident database, but the write-up's sources stand."""
    md = render_markdown(_content("em-abc"), build_context([_email_incident()]))
    cells = [c.strip() for c in md.strip().splitlines()[2].strip("|").split("|")]
    assert cells[0] == "5/14/26"
    assert cells[1] == ""                      # no AIID number
    assert cells[2].startswith("**") and "](" not in cells[2]   # bold, unlinked
    assert "Source: [justice.gov](https://justice.gov/x)" in cells[3]


def test_source_links_are_capped_and_deduplicated():
    links = [
        {"url": "https://a.test/1", "source_domain": "a.test", "title": "one"},
        {"url": "https://a.test/2", "source_domain": "a.test", "title": "two"},
        {"url": "https://b.test/1", "source_domain": "b.test", "title": "three"},
        {"url": "https://c.test/1", "source_domain": "c.test", "title": "four"},
        {"url": "https://d.test/1", "source_domain": "d.test", "title": "five"},
    ]
    context = build_context([_aiid_incident(links=links)], max_sources=3)
    assert [s.label for s in context["1507"].sources] == ["a.test", "b.test", "c.test"]


def test_row_without_a_matching_incident_still_renders():
    """A row an editor typed by hand has no incident record behind it."""
    md = render_markdown(_content("nope"), build_context([]))
    cells = [c.strip() for c in md.strip().splitlines()[2].strip("|").split("|")]
    assert cells[0] == "" and cells[1] == ""
    assert "Source:" not in cells[3]


def test_html_export_escapes_and_links():
    html = render_html(_content("1507", headline="A <script> & B"),
                       build_context([_aiid_incident()]))
    assert "<script>" not in html
    assert "&lt;script&gt; &amp; B" in html
    assert '<a href="https://incidentdatabase.ai/cite/1507">' in html
    assert 'title="CBSE says portal vulnerabilities contained"' in html


def test_text_export_carries_every_cell_for_the_member_scan():
    """The member-mention scan reads this, so no cell may be dropped."""
    text = render_text(_content("1507"), build_context([_aiid_incident()]))
    assert "5/3/26 (AI Incident Database #1507)" in text
    assert "Vendor QA Scripts" in text
    assert "public LLM" in text
    assert "Shadow AI & Supply Chain Vulnerability:" in text
    assert "https://www.thehindu.com/a" in text
