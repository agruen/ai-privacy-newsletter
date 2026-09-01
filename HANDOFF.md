# Handoff — newsletter → table format

**Written 2026-09-01.** The droplet this repo is deployed on does not have enough
RAM to keep running Claude Code, so the remaining work moves to a local machine.
This note is the state of play as of the last commit on `main`.

## What this change does

The issue used to be a written newsletter — editor's note, 3–5 featured stories
with a four-part policy annotation, brief mentions, recommended reading, a
forward-look. It is now a **table**, one row per incident:

| Date | Incident Number | Headline | What Happened | AI Governance Risk Categories |

The model writes only four cells per row (`headline`, `what_happened`,
`risk_category`, `risk_explanation`). The date, the incident number, the AI
Incident Database link behind the headline, and the source links inside "what
happened" are all **joined from our own ingested data at render time**. The model
is never asked for a URL and is explicitly told not to write one, because it
produces plausible URLs that do not resolve.

## Where the pieces are

| File | What changed |
| :--- | :--- |
| `app/ingest/aiid.py` | Reads `reports.csv` out of the AIID snapshot and resolves each incident's report ids into `report_links` on the payload (title, url, source_domain, date). Deduplicated by publication, capped at 6. Missing `reports.csv` degrades to no links. |
| `app/synth/prompts.py` | `NEWSLETTER_SCHEMA` → `DIGEST_TABLE_SCHEMA` (just `rows`). The user prompt asks for a table and forbids URLs. Incidents are shown `reported_by` domains and report headlines but **not** the URLs. |
| `app/synth/ranking.py` | `select()` now returns `(rows, pool)` instead of `(featured, brief, pool)`. Corroboration scoring now counts real reports — the old code split the JSON-list `reports` cell on whitespace and always got 1, so that signal never varied. |
| `app/synth/render.py` | Rewritten around `RowContext` / `Source` and `build_context()`. Markdown, HTML and plain-text renderers all emit the five columns. `format_date()` renders the house `5/3/26` form. |
| `app/synth/compose.py` | Uses the new schema, selection and renderer. `NewsletterItem.role` is now `row` / `pool`. |
| `app/web/routes_newsletters.py` | Row-based edit form; passes `row_ctx` to the template. Also: `_generate_job` now records a `manual_generate` Run so a failed background generate shows on the dashboard instead of only in the container log. |
| `app/scheduler.py` | `_record_run` → `record_run` (now called from the web layer too). |
| `app/web/templates/newsletter_detail.html` | Row editor; shows the non-editable joined columns under each row. Guards drafts written in the old prose format with a warning banner instead of crashing. |
| `app/config.py` | `featured_count` / `brief_count` → `table_rows` (12) and `row_sources_max` (3). |
| `tests/test_table_render.py` | New. Covers the joined columns, both incident sources (AIID and email-submitted), and all three export formats. |

`README.md`, `docs/BUILD_PLAN.md` and `docs/RUNBOOK.md` are updated to describe
the table rather than the prose sections.

## Verification status

- **`pytest -q`: 91 passed**, run in the app image on the droplet against the
  full working tree. Everything below the line is unverified.
- **No live generation has ever been run against the new schema.** No Anthropic
  call has been made with `DIGEST_TABLE_SCHEMA` or the new prompt — the table has
  only ever been rendered from fixtures. Budget for one real `Generate` run being
  the thing that surfaces prompt problems.
- The renderers have not been eyeballed in a browser, only asserted on.

## Open items, in the order they matter

1. **Backfill `report_links` into the already-ingested incidents.** Confirmed on
   the droplet: 1,643 incidents, **0** carry `report_links`. Until this runs, every
   table renders with no source links, because links only appear on incidents
   ingested *after* this change. A backfill script was written on the droplet and
   lost with the scratchpad before it finished — it re-fetched the snapshot,
   rebuilt the id→links index with `AIIDSnapshotConnector`, and wrote only the
   `report_links` key back into each payload. Rewriting it is maybe twenty lines.
   Note the database lives in the `digest_data` Docker volume **on the droplet**,
   so this has to run there, not locally. The alternative is to wait for the next
   weekly snapshot, which backfills the links through the normal upsert path.
2. **Redeploy.** The running container is still at commit `5276bca` — verified
   file-by-file against `HEAD`. It has none of this. Rebuild and
   `docker compose --profile tls up -d --build` on the droplet once the change is
   trusted.
3. **The one existing draft (id 2, period 2026-07) is in the old prose format.**
   The detail template detects this and shows a banner rather than erroring;
   regenerate the period with **Replace existing draft** to convert it, or leave
   it as a historical artifact.
4. **Run a real generation and read the output.** Specifically worth checking:
   whether `risk_category` labels actually repeat across rows where the pattern is
   the same (the prompt asks for it), and whether any row still smuggles a URL or
   a "Source:" prefix into `what_happened` despite the instruction.

## Unrelated, still open

The digest recipient (`APN_DIGEST_TO` / the `digest_to` Setting row) is
deliberately unset. While it is unset `poll_inbox()` returns immediately and
`aiid_check` parks its write-ups. Setting it will bill the whole parked backlog in
one AIID run. Email transport itself is verified working in both directions.
