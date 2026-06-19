# ai-privacy-newsletter

Automated AI privacy incident digest. Ingests AI privacy incidents continuously
(starting with the [AI Incident Database](https://incidentdatabase.ai)) and, on a
monthly trigger, drafts a newsletter for human editorial review. Self-hosted,
fully Dockerized, multi-arch (amd64 + arm64).

This tool does **not** send the newsletter — it produces a reviewable draft and
exports it (Markdown / Rich HTML / Plain text) to paste into the real sending tool.

See [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) for architecture and the phased plan.

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

## Status

Phase 0 (scaffold) complete: FastAPI app, SQLite (WAL) storage, in-process
scheduler with placeholder jobs, multi-arch Docker build, CI. Ingestion,
classification, synthesis, and the review UI follow in later phases.
