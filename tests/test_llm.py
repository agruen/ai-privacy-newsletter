"""Tests for the Anthropic wrapper's model-aware params and response guards.

The Anthropic client is replaced with a fake that records the call params and
returns canned responses, so no network/API key is needed.
"""

import json

from app.llm import AnthropicLLM, LLMError


class _Usage:
    input_tokens = 10
    output_tokens = 5
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, *, stop_reason="end_turn", text='{"ok": true}'):
        self.stop_reason = stop_reason
        self.usage = _Usage()
        self.content = [_TextBlock(text)] if text is not None else []


class _FakeMessages:
    def __init__(self, resp):
        self._resp = resp
        self.captured = None

    def create(self, **kwargs):
        self.captured = kwargs
        return self._resp


class _FakeClient:
    def __init__(self, resp):
        self.messages = _FakeMessages(resp)


def _llm_with(resp):
    llm = AnthropicLLM(api_key="test")  # no network until a call is made
    llm._client = _FakeClient(resp)
    return llm


def test_haiku_call_omits_effort_and_thinking():
    llm = _llm_with(_Resp())
    data, _ = llm.complete_json(
        system="s", user="u", schema={"type": "object"},
        model="claude-haiku-4-5", effort="low",
    )
    assert data == {"ok": True}
    sent = llm._client.messages.captured
    assert "thinking" not in sent                      # would 400 on Haiku 4.5
    assert "effort" not in sent["output_config"]       # would 400 on Haiku 4.5
    assert sent["output_config"]["format"]["type"] == "json_schema"


def test_opus_call_includes_effort_and_thinking():
    llm = _llm_with(_Resp())
    llm.complete_json(
        system="s", user="u", schema={"type": "object"},
        model="claude-opus-4-8", effort="high",
    )
    sent = llm._client.messages.captured
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"]["effort"] == "high"


def test_truncated_response_raises_with_usage():
    llm = _llm_with(_Resp(stop_reason="max_tokens"))
    try:
        llm.complete_json(system="s", user="u", schema={}, model="claude-opus-4-8")
        assert False, "expected LLMError"
    except LLMError as exc:
        assert exc.usage is not None and exc.usage.input_tokens == 10


def test_refusal_raises():
    llm = _llm_with(_Resp(stop_reason="refusal", text=None))
    try:
        llm.complete_json(system="s", user="u", schema={}, model="claude-opus-4-8")
        assert False, "expected LLMError"
    except LLMError:
        pass


def test_invalid_json_raises_with_usage():
    llm = _llm_with(_Resp(text="not json"))
    try:
        llm.complete_json(system="s", user="u", schema={}, model="claude-opus-4-8")
        assert False, "expected LLMError"
    except LLMError as exc:
        assert exc.usage is not None
