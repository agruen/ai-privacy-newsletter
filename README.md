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
# edit .env — at minimum set APN_SECRET_KEY
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
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

- **Daily (no LLM, no cost):** downloads the latest AIID weekly snapshot when a new
  one exists, stores incidents, and flags privacy-relevant ones via AIID's MIT
  "Privacy & Security" taxonomy tag. Pluggable `SourceConnector` / `Classifier`
  interfaces leave room for more sources and an LLM classifier later.
- **Monthly:** ranks the month's privacy incidents, drafts the full newsletter with
  the Anthropic API (editor's note, 3–5 policy-annotated featured stories, brief
  mentions, recommended reading, forward-look), flags any FPF-member mention, and
  presents it for review.
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
