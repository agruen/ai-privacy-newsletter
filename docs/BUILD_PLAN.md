# AI Privacy Incident Digest — Build Plan

A self-hosted pipeline that ingests AI privacy incidents continuously and, on a
monthly trigger, drafts a newsletter for human editorial review. Runs on a small
VPS, fully Dockerized, multi-arch (amd64 + arm64).

This tool does **not** send the newsletter. It produces a reviewable draft and
exports it (Markdown / Rich HTML / Plain text) to be pasted into FPF's real
sending tool.

## Decisions locked

- **Backend:** Python + FastAPI, server-rendered Jinja templates (+ small JS for
  the review UI). One small container.
- **Database:** SQLite (WAL mode) on a mounted volume. No external DB service.
- **Scheduler:** APScheduler in-process (daily ingest + monthly synthesis).
- **LLM:** Anthropic official SDK. Used **only for monthly synthesis** in v1.
- **Auth:** session-cookie, single admin. Password hashed (argon2/bcrypt).
- **Privacy filtering:** pluggable `Classifier` interface. v1 = AIID tag-based
  (no LLM). LLM / ensemble classifiers are future drop-ins.
- **Sources:** AIID only in v1, behind a pluggable `SourceConnector` interface so
  Federal Register / DPA feeds / arXiv / RSS can be added later.
- **Export formats:** Markdown, Rich HTML, Plain text — copy-to-clipboard + download.
- **Member flagging:** admin-managed member list + name/alias match, LLM-confirmed,
  warning banner on the draft.

## Architecture

```
Docker image (multi-arch: linux/amd64 + linux/arm64)
  FastAPI (uvicorn)              APScheduler (in-process)
   - admin auth (session)         - daily:   ingest + classify
   - review UI (Jinja)            - monthly: synthesize draft
   - settings / members CRUD
   - export (MD/HTML/txt)        Anthropic SDK (synthesis only)
        SQLite (WAL)  ->  /data volume (persisted)
  Optional: Caddy sidecar for automatic HTTPS
```

One image, web + scheduler in one process. SQLite WAL handles the light
concurrency. Pure-Python stack → multi-arch is free via `docker buildx`.

## Data model (SQLite)

- `sources` — connector config (AIID first), enabled flag, last cursor.
- `raw_items` — dedup key, source, raw payload (JSON), first-seen date.
- `incidents` — classified items: title, summary, URL(s), date, privacy
  categories, confidence, status (`pending`/`classified`/`needs_review`),
  severity/novelty/regulatory signals, `decided_by`.
- `taxonomy_categories` — editable taxonomy (PII leakage, regurgitation/
  memorization, inferential disclosure, contextual-integrity, agentic
  exfiltration, DSR failures, vendor breach, …).
- `members` — FPF member name + aliases + notes (CRUD + CSV import).
- `member_flags` — per-newsletter: member, section, LLM-confirmed yes/no, snippet.
- `newsletters` — monthly draft: status (`draft`/`approved`), structured content JSON.
- `newsletter_items` — selected/candidate incidents per issue (featured / brief / pool).
- `runs` / `llm_usage` — job logs + token/cost accounting for the budget guard.
- `settings` / `app_user` — config + single admin credential.

Newsletter content is stored as **structured JSON**: editor's note; 3–5 featured
stories each with `{what happened, which mechanism failed, which regime applies,
standard-of-care debate}`; brief mentions; recommended reading; forward-look.
Structured storage makes inline section editing and multi-format export simple.

## Pluggable interfaces

```python
class SourceConnector(Protocol):
    def fetch_new(self, since_cursor) -> list[RawItem]: ...

class Classifier(Protocol):
    def classify(self, item) -> Classification
        # -> { is_privacy, categories, confidence, decided_by }
```

- **v1 `AIIDConnector`** — GraphQL at `https://incidentdatabase.ai/api/graphql`.
- **v1 `AIIDTagClassifier`** — reads incident `classifications`; privacy if a
  configured privacy value is present (MIT AI Risk Repository "Privacy & Security"
  and/or CSET privacy harm). Maps AIID values → our taxonomy. Deterministic,
  confidence 1.0, zero token cost.
- **future** — `LLMClassifier` (Anthropic), `EnsembleClassifier` (tags + LLM tail).

## Pipeline

**Daily (unattended, no LLM in v1):**
1. `AIIDConnector.fetch_new(since)` → raw items. (First task: GraphQL
   introspection to confirm the classifications field path per taxonomy.)
2. Dedupe → store raw.
3. `classifier.classify(...)`. Privacy → `classified`. New incidents AIID hasn't
   classified yet → `pending`, re-checked on later runs (never discarded).

**Monthly (configurable cron + manual "generate now"):**
1. Rank & select by configurable signals (severity, novelty, regulatory weight,
   multi-source narrative).
2. Draft the full issue section-by-section in FPF house style (prompt templates +
   style exemplars).
3. Member-flag pass: name/alias match → LLM confirmation → warning banner.
4. Save as `draft`; surface on dashboard (+ optional SMTP email if creds given).

## Review UI (single admin)

- Dashboard: issues list + latest draft.
- Full draft view, inline-editable per section.
- Swap stories in/out from the candidate pool.
- Member warning banner with jump-to-section.
- Approve → locks the issue.
- Export: Markdown / Rich HTML / Plain text, each copy-to-clipboard + download.
- Admin: members (CRUD + CSV import), taxonomy, sources, settings, manual run
  buttons, run logs, LLM cost meter.

## Cost & ops guards

- Hard `ANTHROPIC_MONTHLY_BUDGET_USD` (default 100). Track per-call cost; pause LLM
  work + flag on dashboard when exceeded.
- Cheap model for any classification, stronger model for synthesis — configurable.
- Fits ≤ $25/mo hosting: one tiny VPS, no managed DB.
- Secrets via env / `.env`, never committed.

## Security

- Session-cookie auth, single admin, hashed password, CSRF on forms, rate-limited
  login. Designed to sit behind Caddy/Traefik for TLS (optional Caddy in compose).

## Build phases

- **Phase 0 — Scaffold:** repo layout, multi-arch Dockerfile, docker-compose
  (+ optional Caddy), FastAPI skeleton, SQLite/migrations, settings, health check,
  CI to build both arches.
- **Phase 1 — Auth + admin shell:** login, single admin, base layout, settings page.
- **Phase 2 — Ingestion:** `SourceConnector` + `AIIDConnector`, GraphQL
  introspection, dedupe, daily job, raw storage, manual "run now".
- **Phase 3 — Classification:** taxonomy CRUD, `Classifier` interface,
  `AIIDTagClassifier`, status/pending handling.
- **Phase 4 — Members:** member CRUD + CSV import, matching utilities.
- **Phase 5 — Synthesis:** ranking, selection, section drafting, member-flag pass,
  prompt templates/exemplars, monthly job, LLM usage + budget guard.
- **Phase 6 — Review UI:** draft view, inline edit, story swap, member banners,
  approve, multi-format export.
- **Phase 7 — Harden + handoff:** runbook, backups, dry-run on synthetic data,
  multi-arch image publish.

Maps to the grant timeline: Week 2 = Phases 2–3, Week 3 = Phases 5–6,
Week 4 = Phase 7. AIID is the single live source; connector + classifier
interfaces are ready for more sources and an LLM classifier later.
