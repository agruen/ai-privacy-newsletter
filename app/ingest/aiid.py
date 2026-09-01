"""AI Incident Database connector.

The AIID GraphQL endpoint is restricted to web browsers, but the project
publishes official weekly database snapshots (openly licensed) as
``backup-<YYYYMMDDHHMMSS>.tar.bz2`` archives. Those archives contain CSV
exports of incidents and per-taxonomy classifications, which is the intended
path for bulk/programmatic access.

This connector finds the latest snapshot, and — only when it is newer than the
stored cursor — downloads it, extracts the incident and MIT-taxonomy
classification CSVs, and yields one RawItem per incident with its MIT
classification attached for the classifier to use.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import httpx

from app.ingest.base import FetchResult, RawItem

logger = logging.getLogger(__name__)

# Stream the snapshot download in chunks so progress can be reported.
_DOWNLOAD_CHUNK = 256 * 1024


def _report(progress: Any | None, **fields) -> None:
    """Push a progress update if a tracker was supplied (no-op otherwise)."""
    if progress is not None:
        progress.update(**fields)


def _fmt_mb(downloaded: int, total: int) -> str:
    mb = downloaded / 1_048_576
    if total:
        return f"{mb:.1f} / {total / 1_048_576:.1f} MB"
    return f"{mb:.1f} MB"

SNAPSHOTS_PAGE = "https://incidentdatabase.ai/research/snapshots/"
CITE_URL = "https://incidentdatabase.ai/cite/{id}"
_SNAPSHOT_RE = re.compile(r'https://[^\s"\']+/backup-(\d{14})\.tar\.bz2')

# CSV members we care about inside the mongodump snapshot.
_INCIDENTS_CSV = "incidents.csv"
_MIT_CSV = "classifications_MIT.csv"
_REPORTS_CSV = "reports.csv"

# Source links kept per incident. The digest table renders the first few as
# "Source: [Publisher](url)"; the rest are dead weight in every payload.
_MAX_REPORT_LINKS = 6


def _parse_report_numbers(raw: str) -> list[str]:
    """The incidents.csv ``reports`` cell is a JSON-ish list: "[2,3,4]"."""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Be forgiving about hand-edited or older formats ("2;3", "2, 3").
        parsed = [p for p in re.split(r"[;,\s]+", raw.strip("[]")) if p]
    if isinstance(parsed, (int, str)):
        parsed = [parsed]          # a lone id: "3" rather than "[3]"
    if not isinstance(parsed, list):
        return []
    return [str(p).strip() for p in parsed if str(p).strip()]


def _index_reports(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """report_number -> the fields the digest needs to cite it."""
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        number = (row.get("report_number") or "").strip()
        url = (row.get("url") or "").strip()
        if not number or not url:
            continue
        index[number] = {
            "title": (row.get("title") or "").strip(),
            "url": url,
            "source_domain": (row.get("source_domain") or "").strip(),
            "date_published": (row.get("date_published") or "").strip(),
        }
    return index


def _report_links(
    reports_cell: str, index: dict[str, dict[str, str]]
) -> list[dict[str, str]]:
    """Resolve an incident's report ids to citable links, in AIID's own order.

    One link per publication: several reports from the same outlet cite the same
    story, and a row that lists one domain three times reads as noise.
    """
    links: list[dict[str, str]] = []
    seen_domains: set[str] = set()
    for number in _parse_report_numbers(reports_cell):
        report = index.get(number)
        if report is None:
            continue
        domain = report["source_domain"].lower().removeprefix("www.")
        if domain and domain in seen_domains:
            continue
        seen_domains.add(domain)
        links.append({**report, "report_number": number})
        if len(links) >= _MAX_REPORT_LINKS:
            break
    return links


class AIIDSnapshotConnector:
    kind = "aiid_snapshot"

    def __init__(
        self,
        source_name: str = "aiid",
        snapshot_file: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.source_name = source_name
        # When set, parse this local archive instead of hitting the network
        # (used by tests and for offline re-imports).
        self.snapshot_file = snapshot_file
        self.timeout = timeout

    # -- snapshot discovery -------------------------------------------------

    def list_snapshots(self, client: httpx.Client) -> list[tuple[str, str]]:
        resp = client.get(SNAPSHOTS_PAGE, timeout=self.timeout)
        resp.raise_for_status()
        found = {(m.group(1), m.group(0)) for m in _SNAPSHOT_RE.finditer(resp.text)}
        return sorted(found, key=lambda t: t[0], reverse=True)

    def latest_snapshot(self, client: httpx.Client) -> tuple[str, str] | None:
        snaps = self.list_snapshots(client)
        return snaps[0] if snaps else None

    # -- fetch --------------------------------------------------------------

    def fetch(self, cursor: str, progress: Any | None = None) -> FetchResult:
        if self.snapshot_file:
            ts = Path(self.snapshot_file).stem  # arbitrary local cursor
            with open(self.snapshot_file, "rb") as fh:
                items = self._parse_archive(fh.read(), progress)
            return FetchResult(items=items, cursor=ts, note=f"local:{self.snapshot_file}")

        with httpx.Client(follow_redirects=True) as client:
            _report(progress, phase="checking", message="Checking for a new snapshot…")
            latest = self.latest_snapshot(client)
            if latest is None:
                return FetchResult(items=[], cursor=cursor, note="no snapshots found")
            ts, url = latest
            if ts == cursor:
                return FetchResult(items=[], cursor=cursor, note="no new snapshot")
            logger.info("downloading AIID snapshot %s", url)
            blob = self._download(client, url, progress)
            items = self._parse_archive(blob, progress)
            return FetchResult(items=items, cursor=ts, note=f"snapshot {ts}")

    def _download(self, client: httpx.Client, url: str, progress: Any | None) -> bytes:
        """Stream the snapshot to memory, reporting bytes downloaded as we go."""
        chunks: list[bytes] = []
        downloaded = 0
        with client.stream("GET", url, timeout=self.timeout) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            _report(
                progress, phase="downloading", download_total=total, downloaded=0,
                message="Downloading snapshot…",
            )
            for chunk in resp.iter_bytes(chunk_size=_DOWNLOAD_CHUNK):
                chunks.append(chunk)
                downloaded += len(chunk)
                _report(
                    progress, downloaded=downloaded,
                    message=f"Downloading snapshot… {_fmt_mb(downloaded, total)}",
                )
        return b"".join(chunks)

    # -- parsing ------------------------------------------------------------

    def _parse_archive(self, blob: bytes, progress: Any | None = None) -> list[RawItem]:
        _report(progress, phase="parsing", message="Parsing snapshot (decompressing)…")
        with tempfile.TemporaryDirectory() as tmp:
            incidents_rows = self._extract_csv(blob, _INCIDENTS_CSV, tmp)
            mit_rows = self._extract_csv(blob, _MIT_CSV, tmp)
            # Optional: older snapshots may not carry reports.csv. Without it we
            # simply have no source links, which the renderer handles.
            try:
                report_rows = self._extract_csv(blob, _REPORTS_CSV, tmp)
            except FileNotFoundError:
                logger.warning("%s not in snapshot; no source links", _REPORTS_CSV)
                report_rows = []

        mit_by_id: dict[str, dict[str, str]] = {}
        for row in mit_rows:
            iid = (row.get("Incident ID") or "").strip()
            if iid:
                mit_by_id[iid] = row

        reports_by_number = _index_reports(report_rows)

        items: list[RawItem] = []
        for row in incidents_rows:
            iid = (row.get("incident_id") or "").strip()
            if not iid:
                continue
            classifications = {}
            if iid in mit_by_id:
                classifications["MIT"] = mit_by_id[iid]
            items.append(
                RawItem(
                    source=self.source_name,
                    external_id=iid,
                    title=(row.get("title") or "").strip(),
                    description=(row.get("description") or "").strip(),
                    url=CITE_URL.format(id=iid),
                    date=(row.get("date") or "").strip(),
                    payload={
                        "incident_id": iid,
                        "deployer": row.get("Alleged deployer of AI system", ""),
                        "developer": row.get("Alleged developer of AI system", ""),
                        "harmed_parties": row.get(
                            "Alleged harmed or nearly harmed parties", ""
                        ),
                        "reports": row.get("reports", ""),
                        # Resolved from reports.csv so the digest can cite real
                        # publications without asking the model for URLs.
                        "report_links": _report_links(
                            row.get("reports", ""), reports_by_number
                        ),
                        "classifications": classifications,
                    },
                )
            )
        logger.info(
            "parsed %d incidents, %d with MIT classification",
            len(items),
            len(mit_by_id),
        )
        _report(progress, parsed=len(items), message=f"Parsed {len(items)} incidents")
        return items

    @staticmethod
    def _extract_csv(blob: bytes, filename: str, tmp: str) -> list[dict[str, str]]:
        """Pull a single CSV (by basename) out of the tar.bz2 archive."""
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:bz2") as tar:
            member = next(
                (m for m in tar.getmembers() if Path(m.name).name == filename), None
            )
            if member is None:
                raise FileNotFoundError(f"{filename} not found in snapshot")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise FileNotFoundError(f"could not read {filename} from snapshot")
            text = extracted.read().decode("utf-8", errors="replace")
        return list(csv.DictReader(io.StringIO(text)))
