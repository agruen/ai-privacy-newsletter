"""Idempotent seeding of default rows (sources, taxonomy) on startup."""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.db import engine
from app.models import Source, TaxonomyCategory

logger = logging.getLogger(__name__)

# Working Paper-defined privacy-incident taxonomy (editable in the admin UI).
DEFAULT_TAXONOMY = [
    ("pii_leakage", "PII leakage", "Exposure of personally identifiable information."),
    ("regurgitation", "Regurgitation / memorization",
     "Model reproduces memorized training data."),
    ("inferential_disclosure", "Inferential disclosure",
     "Sensitive attributes inferred or revealed."),
    ("contextual_integrity", "Contextual-integrity violation",
     "Data used or shared outside its expected context."),
    ("agentic_exfiltration", "Agentic exfiltration via tool calls",
     "An agent leaks data through tool/function use."),
    ("dsr_failure", "Data-subject-rights failure",
     "Failure to honor access, deletion, or correction rights."),
    ("vendor_breach", "Vendor breach involving AI",
     "Breach at a vendor/processor of an AI system."),
    ("security_vuln", "AI system security vulnerability",
     "Security weakness or attack against an AI system."),
    ("other", "Other privacy-relevant", "Privacy-relevant but uncategorized."),
]


def seed_sources() -> None:
    """Ensure the default AIID source exists."""
    with Session(engine) as session:
        if session.get(Source, "aiid") is None:
            session.add(Source(name="aiid", kind="aiid_snapshot", enabled=True))
            session.commit()
            logger.info("seeded default source: aiid")


def seed_taxonomy() -> None:
    """Ensure default taxonomy categories exist (adds any missing)."""
    with Session(engine) as session:
        added = 0
        for i, (key, label, desc) in enumerate(DEFAULT_TAXONOMY):
            if session.get(TaxonomyCategory, key) is None:
                session.add(
                    TaxonomyCategory(
                        key=key, label=label, description=desc, sort_order=i
                    )
                )
                added += 1
        if added:
            session.commit()
            logger.info("seeded %d taxonomy categories", added)
