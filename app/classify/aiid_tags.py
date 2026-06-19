"""Tag-based classifier using AIID's own MIT-taxonomy classifications.

This is the v1 privacy gate: deterministic, zero token cost. It reads the MIT
AI Risk Repository classification attached to each incident during ingestion.
The MIT "2. Privacy & Security" risk domain is the privacy signal; subdomains
map onto our taxonomy categories.

An LLM-based classifier can later be dropped in behind the same interface (or
combined as an ensemble for incidents AIID has not yet classified).
"""

from __future__ import annotations

from typing import Any

from app.classify.base import (
    STATUS_CLASSIFIED,
    STATUS_NOT_PRIVACY,
    STATUS_PENDING,
    Classification,
)

# MIT risk domain that marks privacy relevance.
PRIVACY_DOMAIN_PREFIX = "2."  # "2. Privacy & Security"

# Map MIT subdomain number prefix -> our taxonomy category keys.
SUBDOMAIN_CATEGORY_MAP = {
    "2.1": ["pii_leakage", "inferential_disclosure"],
    "2.2": ["security_vuln"],
}


class AIIDTagClassifier:
    name = "aiid_tags:MIT"

    def classify(self, payload: dict[str, Any]) -> Classification:
        mit = (payload.get("classifications") or {}).get("MIT")
        if not mit:
            # AIID has not classified this incident yet — re-check next snapshot.
            return Classification(status=STATUS_PENDING, decided_by=self.name)

        domain = (mit.get("Risk Domain") or "").strip()
        subdomain = (mit.get("Risk Subdomain") or "").strip()

        if not domain.startswith(PRIVACY_DOMAIN_PREFIX):
            return Classification(
                status=STATUS_NOT_PRIVACY,
                is_privacy=False,
                confidence=1.0,
                decided_by=self.name,
            )

        categories: list[str] = []
        for prefix, keys in SUBDOMAIN_CATEGORY_MAP.items():
            if subdomain.startswith(prefix):
                categories = list(keys)
                break
        if not categories:
            categories = ["other"]

        return Classification(
            status=STATUS_CLASSIFIED,
            is_privacy=True,
            categories=categories,
            confidence=1.0,
            decided_by=self.name,
        )
