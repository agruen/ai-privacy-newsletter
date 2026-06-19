#!/usr/bin/env python
"""Dry-run the pipeline on synthetic data — no network, no API cost.

Builds a small synthetic AIID-style snapshot, runs ingestion + classification,
and prints what the monthly run would rank/select. Use it to rehearse the flow
during training. The actual newsletter writing step requires the Anthropic API;
do one real 'Generate' in the UI to exercise that end to end.

    python scripts/synthetic_dryrun.py
"""

from __future__ import annotations

import io
import os
import sys
import tarfile
import tempfile

# Allow running as `python scripts/synthetic_dryrun.py` without an install.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("APN_DATA_DIR", tempfile.mkdtemp(prefix="apn-dryrun-"))
os.environ.setdefault("APN_SCHEDULER_ENABLED", "false")

from sqlmodel import Session  # noqa: E402

from app.classify.runner import classify_pending  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.ingest.aiid import AIIDSnapshotConnector  # noqa: E402
from app.ingest.runner import _upsert  # noqa: E402
from app.synth.compose import period_incidents  # noqa: E402
from app.synth.ranking import rank, select  # noqa: E402

INCIDENTS_CSV = (
    "_id,incident_id,date,reports,Alleged deployer of AI system,"
    "Alleged developer of AI system,Alleged harmed or nearly harmed parties,"
    "description,title\n"
    "s1,8001,2026-05-04,1;2;3,ChatCo,ChatCo,Users,Model returned other users' chat history,Chatbot leaked private conversations\n"
    "s2,8002,2026-05-12,9,VisionCo,VisionCo,Public,Facial dataset scraped without consent,Face dataset built from scraped photos\n"
    "s3,8003,2026-05-20,4;5,AgentCo,AgentCo,Customers,Agent emailed internal data via a tool call,Agent exfiltrated data through tools\n"
    "s4,8004,2026-05-22,7,SecCo,SecCo,,Prompt-injection bypassed access controls,Security flaw in AI assistant\n"
    "s5,8005,2026-05-28,2,AdCo,AdCo,Users,Spam classifier mislabeled messages,Minor classifier glitch\n"
)

MIT_CSV = (
    "Namespace,Incident ID,Published,Risk Domain,Risk Subdomain,Entity,Timing,Intent\n"
    "MIT,8001,true,2. Privacy & Security,2.1. Compromise of privacy by leaking information,AI,Post-deployment,Unintentional\n"
    "MIT,8002,true,2. Privacy & Security,2.1. Compromise of privacy by obtaining information,AI,Post-deployment,Unintentional\n"
    "MIT,8003,true,2. Privacy & Security,2.1. Compromise of privacy by leaking information,AI,Post-deployment,Unintentional\n"
    "MIT,8004,true,2. Privacy & Security,2.2. AI system security vulnerabilities and attacks,AI,Post-deployment,Intentional\n"
    "MIT,8005,true,3. Misinformation,3.1. False or misleading information,AI,Post-deployment,Unintentional\n"
)


def build_snapshot(path: str) -> None:
    with tarfile.open(path, "w:bz2") as tar:
        for name, content in (
            ("mongodump_full_snapshot/incidents.csv", INCIDENTS_CSV),
            ("mongodump_full_snapshot/classifications_MIT.csv", MIT_CSV),
        ):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def main() -> None:
    settings = get_settings()
    init_db()
    with tempfile.TemporaryDirectory() as tmp:
        snap = os.path.join(tmp, "backup-20260601000000.tar.bz2")
        build_snapshot(snap)
        result = AIIDSnapshotConnector(snapshot_file=snap).fetch("")
        with Session(engine) as session:
            for item in result.items:
                _upsert(session, item)
            session.commit()
            decided = classify_pending(session)
            print(f"Ingested {len(result.items)} incidents; classified {decided}.")

            privacy = period_incidents(session, "2026-05")
            print(f"\nPrivacy incidents for 2026-05: {len(privacy)}")
            ranked = rank(privacy)
            featured, brief, pool = select(
                ranked, settings.featured_count, settings.brief_count
            )
            print("\nWould be FEATURED:")
            for r in featured:
                print(f"  [{r.score:>4}] {r.incident.title}")
            print("\nWould be BRIEF mentions:")
            for r in brief:
                print(f"  [{r.score:>4}] {r.incident.title}")
            print(
                "\nThe monthly run would now draft these with the Anthropic API "
                "and present them for review."
            )


if __name__ == "__main__":
    main()
