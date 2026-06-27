"""In-process progress tracking for the manual ingest job.

Ingest runs as a FastAPI ``BackgroundTask`` inside the single uvicorn worker, so
a thread-safe in-memory record is enough to surface live progress to the Sources
page (which polls ``/sources/status``). This mirrors the in-memory
``LoginThrottle`` in :mod:`app.auth` — no new dependency, and appropriate for a
single-admin, single-process app. State resets on restart, which is fine: a
restart also aborts any in-flight ingest.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IngestProgress:
    """Thread-safe snapshot of the current (or most recent) ingest run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = self._idle()

    @staticmethod
    def _idle() -> dict:
        return {
            "active": False,
            "phase": "idle",          # checking|downloading|parsing|storing|classifying|done
            "message": "No ingest has run yet.",
            "status": "",             # running|ok|error
            "downloaded": 0,          # bytes
            "download_total": 0,      # bytes (0 if server didn't send Content-Length)
            "parsed": 0,              # incidents parsed from the snapshot
            "created": 0,             # incidents newly inserted
            "updated": 0,             # incidents updated
            "classified": 0,          # incidents (re)classified
            "error": "",
            "started_at": None,
            "finished_at": None,
        }

    def is_active(self) -> bool:
        with self._lock:
            return self._state["active"]

    def start(self, message: str = "Starting…") -> None:
        with self._lock:
            self._state = self._idle()
            self._state.update(
                active=True,
                status="running",
                phase="checking",
                message=message,
                started_at=_utcnow_iso(),
            )

    def update(self, **fields) -> None:
        """Merge ``fields`` into the live state (ignored once a run has ended)."""
        with self._lock:
            if not self._state["active"]:
                return
            self._state.update(fields)

    def finish(self, status: str, message: str, error: str = "") -> None:
        with self._lock:
            self._state.update(
                active=False,
                status=status,
                phase="done",
                message=message,
                error=error,
                finished_at=_utcnow_iso(),
            )

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)


# Module-level singleton (single worker → one shared tracker).
INGEST = IngestProgress()
