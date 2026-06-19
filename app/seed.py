"""Idempotent seeding of default rows (sources, taxonomy) on startup."""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.db import engine
from app.models import Source

logger = logging.getLogger(__name__)


def seed_sources() -> None:
    """Ensure the default AIID source exists."""
    with Session(engine) as session:
        if session.get(Source, "aiid") is None:
            session.add(
                Source(
                    name="aiid",
                    kind="aiid_snapshot",
                    enabled=True,
                )
            )
            session.commit()
            logger.info("seeded default source: aiid")
