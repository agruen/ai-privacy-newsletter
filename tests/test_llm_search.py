"""Tests for the web-search completion path and its cost accounting."""

from app.llm import AnthropicLLM, LLMError, Usage, cost_of, merge_usage


class _ServerToolUse:
    def __init__(self, searches):
        self.web_search_requests = searches


class _Usage:
    def __init__(self, searches=0):
        self.input_tokens = 100
        self.output_tokens = 50
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0
        self.server_tool_use = _ServerToolUse(searches) if searches else None


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, *, stop_reason="end_turn", text="notes", searches=0):
        self.stop_reason = stop_reason
        self.usage = _Usage(searches)
        self.content = [_TextBlock(text)] if text is not None else []


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def _llm_with(*responses):
    llm = AnthropicLLM(api_key="test")
    llm._client = _FakeClient(responses)
    return llm


def test_web_searches_are_billed():
    usage = Usage(input_tokens=1000, output_tokens=0, web_searches=3)
    with_searches = cost_of("claude-opus-4-8", usage)
    without = cost_of("claude-opus-4-8", Usage(input_tokens=1000))
    assert abs((with_searches - without) - 0.03) < 1e-9


def test_merge_usage_sums_everything():
    merged = merge_usage(
        Usage(input_tokens=1, output_tokens=2, web_searches=1),
        Usage(input_tokens=10, output_tokens=20, cache_read_tokens=5,
              web_searches=2),
    )
    assert merged.input_tokens == 11 and merged.output_tokens == 22
    assert merged.cache_read_tokens == 5 and merged.web_searches == 3


def test_search_call_sends_web_tool_and_returns_text():
    llm = _llm_with(_Resp(text="CONFIRMED FACTS", searches=2))
    text, usage = llm.complete_text_with_search(
        system="s", user="u", model="claude-opus-4-8",
    )
    assert text == "CONFIRMED FACTS"
    assert usage.web_searches == 2
    sent = llm._client.messages.calls[0]
    assert sent["tools"] == [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
    ]
    assert sent["thinking"] == {"type": "adaptive"}


def test_older_model_gets_basic_search_tool_without_thinking():
    llm = _llm_with(_Resp())
    llm.complete_text_with_search(system="s", user="u", model="claude-haiku-4-5")
    sent = llm._client.messages.calls[0]
    assert sent["tools"][0]["type"] == "web_search_20250305"
    assert "thinking" not in sent and "output_config" not in sent


def test_pause_turn_is_resumed_and_usage_accumulates():
    paused = _Resp(stop_reason="pause_turn", text="partial", searches=1)
    done = _Resp(text="final notes", searches=1)
    llm = _llm_with(paused, done)

    text, usage = llm.complete_text_with_search(
        system="s", user="u", model="claude-opus-4-8",
    )
    assert text == "final notes"
    assert usage.input_tokens == 200 and usage.web_searches == 2
    calls = llm._client.messages.calls
    assert len(calls) == 2
    # The resume re-sends the paused assistant turn for continuation.
    assert calls[1]["messages"][1]["role"] == "assistant"


def test_search_refusal_raises_with_accumulated_usage():
    llm = _llm_with(_Resp(stop_reason="refusal", text=None))
    try:
        llm.complete_text_with_search(system="s", user="u",
                                      model="claude-opus-4-8")
        assert False, "expected LLMError"
    except LLMError as exc:
        assert exc.usage is not None and exc.usage.input_tokens == 100
