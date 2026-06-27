"""Anthropic client wrapper: structured JSON calls, cost accounting, budget guard.

LLM usage is confined to monthly synthesis in v1. The client returns the parsed
JSON plus the raw usage object; the synthesis layer records cost and enforces the
monthly budget so this wrapper stays thin and easy to fake in tests.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from sqlmodel import Session, select

from app.models import LLMUsage, utcnow

logger = logging.getLogger(__name__)

# USD per 1M tokens: (input, output). Cache reads ~0.1x input, writes ~1.25x.
PRICING = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}
_DEFAULT_PRICE = (5.0, 25.0)


class BudgetExceeded(Exception):
    """Raised when the configured monthly LLM budget would be exceeded."""


class LLMError(Exception):
    """The model call returned something we can't use (refusal, truncation, bad JSON).

    Carries the token usage when available so the caller can still record the
    spend that was incurred before the response failed to parse.
    """

    def __init__(self, message: str, usage: "Usage | None" = None) -> None:
        super().__init__(message)
        self.usage = usage


# Models that accept output_config.effort and adaptive thinking. Per the Claude
# API capability reference, effort and adaptive thinking both error on
# Haiku 4.5 / Sonnet 4.5; structured outputs (output_config.format) work there.
EFFORT_THINKING_MODELS = {
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-fable-5",
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def _usage_from_response(raw: Any) -> Usage:
    return Usage(
        input_tokens=getattr(raw, "input_tokens", 0) or 0,
        output_tokens=getattr(raw, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
    )


def cost_of(model: str, usage: Usage) -> float:
    in_price, out_price = PRICING.get(model, _DEFAULT_PRICE)
    return (
        usage.input_tokens * in_price
        + usage.cache_read_tokens * in_price * 0.1
        + usage.cache_write_tokens * in_price * 1.25
        + usage.output_tokens * out_price
    ) / 1_000_000


def month_spend(session: Session) -> float:
    """Sum of LLM cost recorded in the current UTC calendar month."""
    now = utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = session.exec(select(LLMUsage).where(LLMUsage.ts >= start)).all()
    return sum(r.cost_usd for r in rows)


def record_usage(
    session: Session,
    *,
    purpose: str,
    model: str,
    usage: Usage,
    newsletter_id: int | None = None,
) -> float:
    cost = cost_of(model, usage)
    session.add(
        LLMUsage(
            purpose=purpose,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cost_usd=cost,
            newsletter_id=newsletter_id,
        )
    )
    session.commit()
    return cost


class LLM(Protocol):
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict,
        model: str,
        effort: str = "high",
        max_tokens: int = 16000,
    ) -> tuple[dict, Usage]: ...


# Above this output size the SDK requires streaming (non-streaming requests it
# estimates will exceed ~10 minutes are refused).
_STREAM_THRESHOLD = 16000


class AnthropicLLM:
    """Thin wrapper over the Anthropic SDK returning validated JSON + usage."""

    def __init__(self, api_key: str) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict,
        model: str,
        effort: str = "high",
        max_tokens: int = 16000,
    ) -> tuple[dict, Usage]:
        output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
        params: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        # effort + adaptive thinking error on models that don't support them
        # (e.g. the cheap Haiku confirm model); send them only where valid.
        if model in EFFORT_THINKING_MODELS:
            params["thinking"] = {"type": "adaptive"}
            output_config["effort"] = effort

        if max_tokens > _STREAM_THRESHOLD:
            with self._client.messages.stream(**params) as stream:
                resp = stream.get_final_message()
        else:
            resp = self._client.messages.create(**params)

        usage = _usage_from_response(resp.usage)
        stop = getattr(resp, "stop_reason", None)
        if stop == "refusal":
            raise LLMError("model refused the request", usage)
        if stop == "max_tokens":
            raise LLMError("response truncated (max_tokens) — raise max_tokens", usage)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        if not text.strip():
            raise LLMError(f"empty/non-text response (stop_reason={stop})", usage)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"model returned invalid JSON: {exc}", usage) from exc
        return data, usage


def get_llm(api_key: str) -> LLM:
    if not api_key:
        raise RuntimeError("ANTHROPIC API key is not configured (APN_ANTHROPIC_API_KEY)")
    return AnthropicLLM(api_key)
