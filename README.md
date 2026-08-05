# ai-privacy-newsletter

Automated AI privacy incident digest. Ingests AI privacy incidents continuously
(starting with the [AI Incident Database](https://incidentdatabase.ai)) and, on a
monthly trigger, drafts a newsletter for human editorial review. Self-hosted,
fully Dockerized, multi-arch (amd64 + arm64).

This tool does **not** send the newsletter — it produces a reviewable draft and
exports it (Markdown / Rich HTML / Plain text) to paste into the real sending tool.

See [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) for architecture and the phased
plan, and [`docs/RUNBOOK.md`](docs/RUNBOOK.md) for day-to-day operations.

## Quickstart (Docker)

```bash
cp .env.example .env
# edit .env — set at least:
#   APN_SECRET_KEY    (required in production; >=32 random chars)
#     python -c "import secrets; print(secrets.token_urlsafe(48))"
#   APN_ADMIN_PASSWORD  (required on first boot to create the admin login)
docker compose up -d --build
```

App is at http://localhost:8000 (health check: `/healthz`).

For automatic HTTPS behind Caddy, set `DOMAIN` and use the `tls` profile:

```bash
DOMAIN=digest.example.org docker compose --profile tls up -d --build
```

## Local development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install ".[dev]"
APN_DATA_DIR=./data APN_SCHEDULER_ENABLED=false \
  uvicorn app.main:app --reload
pytest -q
```

## How it works

- **Ingest (button, no LLM, no cost):** click **Run ingest now** on the Sources page
  to download the latest AIID weekly snapshot (when a new one exists), store
  incidents, and flag privacy-relevant ones via AIID's MIT "Privacy & Security"
  taxonomy tag. Pluggable `SourceConnector` / `Classifier` interfaces leave room for
  more sources and an LLM classifier later.
- **Generate (button):** click **Generate** on the Newsletters page to rank the
  month's privacy incidents and draft the full newsletter with the Anthropic API
  (editor's note, 3–5 policy-annotated featured stories, brief mentions, recommended
  reading, forward-look), flag any FPF-member mention, and present it for review. A
  month that already has a draft is not re-billed unless you tick **Replace existing
  draft**.
- **Email channel (optional, hands-off):** fill in the `APN_IMAP_*` settings and a
  digest recipient, and the app runs itself over email — no login needed:
  - **Inbox intake:** every 5 minutes it checks a dedicated IMAP mailbox with a
    deterministic UID cursor (LLM only ever reads genuinely new mail; bounces,
    auto-replies, and duplicates are dropped by header checks for free). A message
    judged to report an AI privacy incident is fact-checked with Anthropic's web
    search — uncorroborated claims stay attributed to the submitter, never stated
    as fact — written up in the house format, and emailed to the recipient.
  - **AIID watch:** daily at noon Washington DC time it checks for a new AIID
    snapshot (no-op most days; AIID publishes weekly), screens only
    never-before-seen incidents for a privacy angle, and sends one digest email
    per run with the write-ups. The first run establishes a baseline so the
    historical backlog is never emailed.
  - **Review log:** the **Activity** page records every inbound email (and what
    was decided about it), every outbound send, and every run.
- **Automation (optional):** the app runs on demand by default. Set
  `APN_SCHEDULER_ENABLED=true` to also run ingest daily and generation monthly on a
  cron (`APN_DAILY_INGEST_CRON` / `APN_MONTHLY_SYNTH_CRON`, UTC).
- **Review:** a single-admin web UI to edit every section inline, swap stories from
  the candidate pool, heed member-mention warnings, approve, and export
  (Markdown / Rich HTML / Plain text). It does not send — output only.
- **Cost control:** a hard `APN_ANTHROPIC_MONTHLY_BUDGET_USD` (default $100) with a
  live spend meter on the dashboard.

## Operations

- Runbook: [`docs/RUNBOOK.md`](docs/RUNBOOK.md)
- Backups: `scripts/backup.sh` (SQLite online backup; schedule via host cron)
- Training dry-run (no API cost): `python scripts/synthetic_dryrun.py`
- Published images: tagging `vX.Y.Z` builds a multi-arch image to
  `ghcr.io/<owner>/ai-privacy-newsletter`.

## Status

Complete end to end: scaffold, auth, AIID ingestion, tag classification, member
list + matching, monthly synthesis with cost guard, the review UI with export, and
the operations runbook. All phases are covered by tests and a green CI
(pytest + multi-arch Docker build).
