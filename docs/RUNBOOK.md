# Operations Runbook — AI Privacy Incident Digest

Audience: the FPF program manager who owns the monthly newsletter review and
day-to-day stewardship. Typical effort: about half a day per month.

## What the system does (one paragraph)

Every day it downloads the latest [AI Incident Database](https://incidentdatabase.ai)
snapshot (only when a new weekly one exists), stores incidents, and marks the
privacy-relevant ones using AIID's own MIT-taxonomy "Privacy & Security" tag — no
LLM, no cost. Once a month it ranks that month's privacy incidents, drafts a full
newsletter with Anthropic's API, flags any mention of an FPF member, and presents
the draft for your review. It never sends anything — you export and paste into the
FPF sending tool.

## Monthly review (the main task)

1. On the **Dashboard**, confirm the month's draft was generated (or open
   **Newsletters** and click **Generate** for the month — it runs in the
   background; refresh in a minute).
2. Open the draft. **Read the member-mention banner first** — anything flagged
   "about this member" needs careful editorial judgment before publishing.
3. Edit any section inline: editor's note, the featured stories (headline + what
   happened / mechanism that failed / regime that applies / standard-of-care
   debate), brief mentions, recommended reading, forward-look. Remove weak
   stories; pull a stronger one from the **candidate pool** by adding a featured
   story and writing it up.
4. Click **Save changes** as you go.
5. When satisfied, click **Approve** (locks the issue). Use **Reopen** if you need
   to edit after approving.
6. Open **Export**, copy the format the FPF sending tool wants (Markdown / Rich
   HTML / Plain text), or download the file, and paste it into the sending tool.

## Configuration

All settings are environment variables (prefix `APN_`), shown read-only on the
**Settings** page. Change them in `.env` and restart the container.

- `APN_ANTHROPIC_API_KEY` — required for the monthly draft.
- `APN_ANTHROPIC_MONTHLY_BUDGET_USD` (default 100) — hard cap. When reached, LLM
  work pauses and the dashboard shows it; daily ingestion is unaffected (it uses
  no LLM).
- `APN_ANTHROPIC_MODEL` (default `claude-opus-4-8`) — synthesis model.
- `APN_FEATURED_COUNT` / `APN_BRIEF_COUNT` — issue size.
- `APN_DAILY_INGEST_CRON` / `APN_MONTHLY_SYNTH_CRON` — schedules (UTC cron).

## Re-tuning

- **Voice / framing drift.** Edit the FPF style guide by setting a `style_guide`
  value (Settings → key/value) or adjust `app/synth/prompts.py`
  (`DEFAULT_STYLE_GUIDE`). Re-generate the month to compare.
- **Wrong stories chosen.** Adjust ranking weights in `app/synth/ranking.py`
  (`CATEGORY_WEIGHT`) or change `APN_FEATURED_COUNT`.
- **Privacy filter too narrow/broad.** v1 uses AIID's MIT "Privacy & Security"
  tag (see `app/classify/aiid_tags.py`). To change what counts as privacy, edit
  `SUBDOMAIN_CATEGORY_MAP` / `PRIVACY_DOMAIN_PREFIX`. An LLM classifier can be
  added behind the same `Classifier` interface later.
- **Taxonomy.** Add/edit categories on the **Taxonomy** page.

## Handling source outages

- AIID publishes weekly; a daily run with no new snapshot is a no-op (Sources page
  shows "no new snapshot"). That is normal.
- If ingestion shows `error` on the **Sources** page (AIID download failed, format
  change), the last good data is retained. Click **Run ingest now** to retry; if it
  keeps failing, the snapshot URL/format may have changed — check
  `https://incidentdatabase.ai/research/snapshots/` and `app/ingest/aiid.py`.
- New incidents AIID hasn't classified yet sit in **pending** and are re-checked on
  the next snapshot — they are never lost.

## Escalation to the Working Paper team

Escalate (quarterly advisory call or sooner) when: a high-profile incident needs
framing help; the taxonomy needs refreshing; a new source should be added; or the
classifier/prompts need substantive changes beyond the knobs above.

## Backups

The entire state is one SQLite file in the `/data` volume. Back it up with
`scripts/backup.sh` (uses SQLite's online `.backup`, safe while running). Schedule
it via cron on the host. To restore, stop the container, replace
`digest.db` in the volume, and start again.

## Dry run (training)

To rehearse without waiting for real data, see `scripts/synthetic_dryrun.py` — it
loads a synthetic snapshot, runs ingestion + classification (no API cost), and
prints what the monthly run would select. Then do one real **Generate** against a
recent month to exercise the writing + review + export path end to end.
