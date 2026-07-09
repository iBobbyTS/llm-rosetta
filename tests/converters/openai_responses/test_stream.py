"""
OpenAI Responses API stream converter unit tests.
"""

from typing import Any, cast

from codex_rosetta.converters.openai_chat import OpenAIChatConverter
from codex_rosetta.converters.openai_responses import OpenAIResponsesConverter
from codex_rosetta.converters.openai_responses.stream_context import (
    OpenAIResponsesStreamContext,
)
from codex_rosetta.types.ir.stream import (
    ContentBlockEndEvent,
    ContentBlockStartEvent,
    FinishEvent,
    ReasoningDeltaEvent,
    StreamEndEvent,
    StreamStartEvent,
    TextDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallStartEvent,
    UsageEvent,
)


class TestStreamResponseFromProvider:
    """Tests for stream_response_from_provider."""

    def setup_method(self):
        self.converter = OpenAIResponsesConverter()

    # --- Text delta ---

    def test_text_delta(self):
        """response.output_text.delta produces TextDeltaEvent."""
        event = {
            "type": "response.output_text.delta",
            "delta": "Hello",
            "output_index": 0,
            "content_index": 0,
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        assert len(events) == 1
        assert events[0]["type"] == "text_delta"
        assert events[0]["text"] == "Hello"

    def test_text_delta_empty_string(self):
        """Empty text delta still produces an event."""
        event = {
            "type": "response.output_text.delta",
            "delta": "",
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        assert len(events) == 1
        assert events[0]["type"] == "text_delta"
        assert events[0]["text"] == ""

    # --- Reasoning delta ---

    def test_reasoning_summary_delta(self):
        """response.reasoning_summary_text.delta produces ReasoningDeltaEvent."""
        event = {
            "type": "response.reasoning_summary_text.delta",
            "delta": "Let me think...",
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        assert len(events) == 1
        assert events[0]["type"] == "reasoning_delta"
        assert events[0]["reasoning"] == "Let me think..."

    # --- Tool call start ---

    def test_tool_call_start_function_call(self):
        """response.output_item.added with function_call produces ToolCallStartEvent."""
        event = {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {
                "type": "function_call",
                "call_id": "call_abc",
                "name": "get_weather",
                "arguments": "",
            },
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        assert len(events) == 1
        assert events[0]["type"] == "tool_call_start"
        assert events[0]["tool_call_id"] == "call_abc"
        assert events[0]["tool_name"] == "get_weather"
        assert events[0]["tool_call_index"] == 1

    def test_output_item_added_non_function_call(self):
        """response.output_item.added with non-function_call type produces no events."""
        event = {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [],
            },
        }
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    def test_tool_call_start_no_output_index(self):
        """ToolCallStartEvent without output_index omits tool_call_index."""
        event = {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "call_id": "call_xyz",
                "name": "search",
            },
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        assert len(events) == 1
        assert "tool_call_index" not in events[0]

    # --- Tool call arguments delta ---

    def test_tool_call_arguments_delta(self):
        """response.function_call_arguments.delta produces ToolCallDeltaEvent."""
        event = {
            "type": "response.function_call_arguments.delta",
            "call_id": "call_abc",
            "delta": '{"city":',
            "output_index": 1,
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        assert len(events) == 1
        assert events[0]["type"] == "tool_call_delta"
        assert events[0]["tool_call_id"] == "call_abc"
        assert events[0]["arguments_delta"] == '{"city":'
        assert events[0]["tool_call_index"] == 1

    def test_tool_call_arguments_delta_no_output_index(self):
        """ToolCallDeltaEvent without output_index omits tool_call_index."""
        event = {
            "type": "response.function_call_arguments.delta",
            "call_id": "call_abc",
            "delta": '{"x":1}',
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        assert len(events) == 1
        assert "tool_call_index" not in events[0]

    # --- Response completed ---

    def test_response_completed_stop(self):
        """response.completed with status 'completed' produces FinishEvent with 'stop'."""
        event = {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ],
            },
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        finish_events = [e for e in events if e["type"] == "finish"]
        assert len(finish_events) == 1
        assert finish_events[0]["finish_reason"]["reason"] == "stop"

    def test_response_completed_with_tool_calls(self):
        """response.completed with function_call output sets reason to 'tool_calls'."""
        event = {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "search",
                        "arguments": "{}",
                    }
                ],
            },
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        finish_events = [e for e in events if e["type"] == "finish"]
        assert finish_events[0]["finish_reason"]["reason"] == "tool_calls"

    def test_response_completed_incomplete_max_tokens(self):
        """response.completed with incomplete status and max_output_tokens reason."""
        event = {
            "type": "response.completed",
            "response": {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [],
            },
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        finish_events = [e for e in events if e["type"] == "finish"]
        assert finish_events[0]["finish_reason"]["reason"] == "length"

    def test_response_completed_incomplete_content_filter(self):
        """response.completed with incomplete status and content_filter reason."""
        event = {
            "type": "response.completed",
            "response": {
                "status": "incomplete",
                "incomplete_details": {"reason": "content_filter"},
                "output": [],
            },
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        finish_events = [e for e in events if e["type"] == "finish"]
        assert finish_events[0]["finish_reason"]["reason"] == "content_filter"

    def test_response_completed_with_usage(self):
        """response.completed with usage produces both FinishEvent and UsageEvent."""
        event = {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
            },
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        types = [e["type"] for e in events]
        assert "finish" in types
        assert "usage" in types
        usage_event = [e for e in events if e["type"] == "usage"][0]
        assert usage_event["usage"]["prompt_tokens"] == 10
        assert usage_event["usage"]["completion_tokens"] == 5
        assert usage_event["usage"]["total_tokens"] == 15

    # --- Response failed ---

    def test_response_failed(self):
        """response.failed produces FinishEvent with 'error'."""
        event = {
            "type": "response.failed",
            "response": {
                "status": "failed",
                "error": {"message": "Something went wrong"},
            },
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        assert len(events) == 1
        assert events[0]["type"] == "finish"
        assert events[0]["finish_reason"]["reason"] == "error"

    # --- Ignored events ---

    def test_response_created_ignored(self):
        """response.created produces no events."""
        event = {"type": "response.created", "response": {}}
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    def test_response_in_progress_ignored(self):
        """response.in_progress produces no events."""
        event = {"type": "response.in_progress", "response": {}}
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    def test_output_item_done_ignored(self):
        """response.output_item.done produces no events."""
        event = {"type": "response.output_item.done", "item": {}}
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    def test_content_part_added_ignored(self):
        """response.content_part.added produces no events."""
        event = {"type": "response.content_part.added", "part": {}}
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    def test_output_text_done_ignored(self):
        """response.output_text.done produces no events."""
        event = {"type": "response.output_text.done", "text": "final"}
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    def test_function_call_arguments_done_ignored(self):
        """response.function_call_arguments.done produces no events."""
        event = {
            "type": "response.function_call_arguments.done",
            "arguments": "{}",
        }
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    def test_unknown_event_ignored(self):
        """Unknown event type produces no events."""
        event = {"type": "some.unknown.event"}
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    # --- SDK object normalization ---

    def test_normalize_sdk_object(self):
        """SDK objects with model_dump() are normalized."""

        class MockEvent:
            def model_dump(self):
                return {
                    "type": "response.output_text.delta",
                    "delta": "sdk",
                }

        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(
                cast(dict[str, Any], MockEvent())
            ),
        )
        assert len(events) == 1
        assert events[0]["text"] == "sdk"


class TestStreamResponseToProvider:
    """Tests for stream_response_to_provider."""

    def setup_method(self):
        self.converter = OpenAIResponsesConverter()

    def test_text_delta(self):
        """TextDeltaEvent → response.output_text.delta."""
        event = cast(TextDeltaEvent, {"type": "text_delta", "text": "Hello"})
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result["type"] == "response.output_text.delta"
        assert result["delta"] == "Hello"

    def test_reasoning_delta(self):
        """ReasoningDeltaEvent → response.reasoning_summary_text.delta."""
        event = cast(
            ReasoningDeltaEvent,
            {"type": "reasoning_delta", "reasoning": "thinking..."},
        )
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result["type"] == "response.reasoning_summary_text.delta"
        assert result["delta"] == "thinking..."

    def test_reasoning_delta_preserved_in_completed_output(self):
        """Reasoning deltas become durable reasoning output items."""
        ctx = OpenAIResponsesStreamContext()

        result = cast(
            list[dict[str, Any]],
            self.converter.stream_response_to_provider(
                cast(ReasoningDeltaEvent, {"type": "reasoning_delta", "reasoning": ""}),
                context=ctx,
            ),
        )
        assert result[0]["type"] == "response.output_item.added"
        assert result[0]["output_index"] == 0
        assert result[0]["item"]["type"] == "reasoning"
        assert result[0]["item"]["status"] == "in_progress"
        assert result[1]["type"] == "response.reasoning_summary_text.delta"
        assert result[1]["delta"] == ""

        self.converter.stream_response_to_provider(
            cast(
                ReasoningDeltaEvent,
                {"type": "reasoning_delta", "reasoning": "thinking..."},
            ),
            context=ctx,
        )
        results = cast(
            list[dict[str, Any]],
            self.converter.stream_response_to_provider(
                cast(
                    FinishEvent,
                    {"type": "finish", "finish_reason": {"reason": "tool_calls"}},
                ),
                context=ctx,
            ),
        )

        assert len(results) == 1
        assert results[0]["type"] == "response.output_item.done"
        assert results[0]["output_index"] == 0
        assert results[0]["item"]["type"] == "reasoning"
        assert results[0]["item"]["status"] == "completed"
        assert ctx.pending_response is not None
        output = ctx.pending_response["output"]
        assert output[0]["type"] == "reasoning"
        assert output[0]["summary"] == [{"type": "summary_text", "text": "thinking..."}]
        assert output[0]["status"] == "completed"

    def test_tool_call_start(self):
        """ToolCallStartEvent → response.output_item.added."""
        event = cast(
            ToolCallStartEvent,
            {
                "type": "tool_call_start",
                "tool_call_id": "call_abc",
                "tool_name": "search",
                "tool_call_index": 1,
            },
        )
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result["type"] == "response.output_item.added"
        assert result["item"]["id"] == "call_abc"
        assert result["item"]["type"] == "function_call"
        assert result["item"]["call_id"] == "call_abc"
        assert result["item"]["name"] == "search"
        assert result["output_index"] == 1

    def test_tool_call_start_no_index(self):
        """ToolCallStartEvent without tool_call_index defaults output_index to 0."""
        event = cast(
            ToolCallStartEvent,
            {
                "type": "tool_call_start",
                "tool_call_id": "call_abc",
                "tool_name": "search",
            },
        )
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result["item"]["id"] == "call_abc"
        assert result["output_index"] == 0

    def test_tool_call_delta(self):
        """ToolCallDeltaEvent → response.function_call_arguments.delta."""
        event = cast(
            ToolCallDeltaEvent,
            {
                "type": "tool_call_delta",
                "tool_call_id": "call_abc",
                "arguments_delta": '{"city":',
                "tool_call_index": 1,
            },
        )
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result["type"] == "response.function_call_arguments.delta"
        assert result["item_id"] == "call_abc"
        assert result["delta"] == '{"city":'
        assert result["output_index"] == 1

    def test_tool_call_delta_no_index(self):
        """ToolCallDeltaEvent without tool_call_index defaults output_index to 0."""
        event = cast(
            ToolCallDeltaEvent,
            {
                "type": "tool_call_delta",
                "tool_call_id": "call_abc",
                "arguments_delta": "{}",
            },
        )
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result["item_id"] == "call_abc"
        assert result["output_index"] == 0

    def test_tool_call_delta_empty_id_resolved_by_index(self):
        """ToolCallDeltaEvent with empty tool_call_id resolved via context index."""
        ctx = OpenAIResponsesStreamContext()
        # Simulate a prior tool_call_start that registered the call
        ctx.register_tool_call("call_abc", "get_weather")
        ctx.register_tool_call_item("call_abc", "call_abc")

        event = cast(
            ToolCallDeltaEvent,
            {
                "type": "tool_call_delta",
                "tool_call_id": "",
                "arguments_delta": '{"city":"Beijing"}',
                "tool_call_index": 0,
            },
        )
        result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(event, context=ctx),
        )
        assert result["type"] == "response.function_call_arguments.delta"
        assert result["item_id"] == "call_abc"
        assert result["delta"] == '{"city":"Beijing"}'
        # Verify args were accumulated under the resolved call_id
        assert ctx.get_tool_call_args("call_abc") == '{"city":"Beijing"}'

    def test_finish_event_stop(self):
        """FinishEvent with 'stop' → response.completed with status 'completed'."""
        event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "stop"}},
        )
        results = cast(
            list[dict[str, Any]], self.converter.stream_response_to_provider(event)
        )
        completed = next(r for r in results if r["type"] == "response.completed")
        assert completed["response"]["status"] == "completed"

    def test_finish_event_length(self):
        """FinishEvent with 'length' → response.completed with status 'incomplete'."""
        event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "length"}},
        )
        results = cast(
            list[dict[str, Any]], self.converter.stream_response_to_provider(event)
        )
        completed = next(r for r in results if r["type"] == "response.completed")
        assert completed["response"]["status"] == "incomplete"
        assert (
            completed["response"]["incomplete_details"]["reason"] == "max_output_tokens"
        )

    def test_finish_event_error(self):
        """FinishEvent with 'error' → response.completed with status 'failed'."""
        event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "error"}},
        )
        results = cast(
            list[dict[str, Any]], self.converter.stream_response_to_provider(event)
        )
        completed = next(r for r in results if r["type"] == "response.completed")
        assert completed["response"]["status"] == "failed"

    def test_finish_event_content_filter(self):
        """FinishEvent with 'content_filter' → response.completed with status 'incomplete'."""
        event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "content_filter"}},
        )
        results = cast(
            list[dict[str, Any]], self.converter.stream_response_to_provider(event)
        )
        completed = next(r for r in results if r["type"] == "response.completed")
        assert completed["response"]["status"] == "incomplete"
        assert completed["response"]["incomplete_details"] == {
            "reason": "content_filter"
        }

    def test_finish_event_tool_calls(self):
        """FinishEvent with 'tool_calls' → response.completed with status 'completed'."""
        event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "tool_calls"}},
        )
        results = cast(
            list[dict[str, Any]], self.converter.stream_response_to_provider(event)
        )
        completed = next(r for r in results if r["type"] == "response.completed")
        assert completed["response"]["status"] == "completed"

    def test_usage_event(self):
        """UsageEvent → response.completed with usage."""
        event = cast(
            UsageEvent,
            {
                "type": "usage",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result["type"] == "response.completed"
        assert result["response"]["usage"]["input_tokens"] == 10
        assert result["response"]["usage"]["output_tokens"] == 5
        assert result["response"]["usage"]["total_tokens"] == 15

    def test_unknown_event_type(self):
        """Unknown event type returns empty dict."""
        event = cast(TextDeltaEvent, {"type": "unknown_event"})
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result == {}


class TestStreamRoundTrip:
    """Round-trip tests: provider → IR → provider."""

    def setup_method(self):
        self.converter = OpenAIResponsesConverter()

    def test_text_delta_round_trip(self):
        """Text delta round-trip preserves content."""
        original = {
            "type": "response.output_text.delta",
            "delta": "Hello",
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(original))
        restored = cast(
            dict[str, Any], self.converter.stream_response_to_provider(events[0])
        )
        assert restored["type"] == "response.output_text.delta"
        assert restored["delta"] == "Hello"

    def test_reasoning_delta_round_trip(self):
        """Reasoning delta round-trip preserves content."""
        original = {
            "type": "response.reasoning_summary_text.delta",
            "delta": "step 1",
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(original))
        restored = cast(
            dict[str, Any], self.converter.stream_response_to_provider(events[0])
        )
        assert restored["type"] == "response.reasoning_summary_text.delta"
        assert restored["delta"] == "step 1"

    def test_tool_call_start_round_trip(self):
        """Tool call start round-trip preserves id and name."""
        original = {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {
                "type": "function_call",
                "call_id": "call_abc",
                "name": "search",
            },
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(original))
        restored = cast(
            dict[str, Any], self.converter.stream_response_to_provider(events[0])
        )
        assert restored["item"]["id"] == "call_abc"
        assert restored["item"]["call_id"] == "call_abc"
        assert restored["item"]["name"] == "search"
        assert restored["output_index"] == 1

    def test_tool_call_delta_round_trip(self):
        """Tool call delta round-trip preserves arguments."""
        original = {
            "type": "response.function_call_arguments.delta",
            "call_id": "call_abc",
            "delta": '{"q": "test"}',
            "output_index": 1,
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(original))
        restored = cast(
            dict[str, Any], self.converter.stream_response_to_provider(events[0])
        )
        assert restored["item_id"] == "call_abc"
        assert restored["delta"] == '{"q": "test"}'


class TestStreamResponseFromProviderWithContext:
    """Tests for stream_response_from_provider with StreamContext."""

    def setup_method(self):
        self.converter = OpenAIResponsesConverter()

    def test_response_created_emits_stream_start(self):
        """response.created with context emits StreamStartEvent."""
        ctx = OpenAIResponsesStreamContext()
        event = {
            "type": "response.created",
            "response": {
                "id": "resp_abc123",
                "model": "gpt-4o",
                "created_at": 1700000000,
                "status": "in_progress",
                "output": [],
            },
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        assert len(events) == 1
        assert events[0]["type"] == "stream_start"
        assert events[0]["response_id"] == "resp_abc123"
        assert events[0]["model"] == "gpt-4o"
        assert events[0]["created"] == 1700000000
        assert ctx.response_id == "resp_abc123"
        assert ctx.model == "gpt-4o"
        assert ctx.is_started is True

    def test_response_created_without_context_no_events(self):
        """response.created without context produces no events (backward compat)."""
        event = {
            "type": "response.created",
            "response": {
                "id": "resp_abc123",
                "model": "gpt-4o",
            },
        }
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    def test_response_completed_emits_stream_end(self):
        """response.completed with context emits StreamEndEvent after other events."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()
        event = {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
            },
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        types = [e["type"] for e in events]
        assert "finish" in types
        assert "usage" in types
        assert "stream_end" in types
        # StreamEndEvent must be last
        assert types[-1] == "stream_end"
        assert ctx.is_ended is True

    def test_response_completed_with_tool_search_sets_tool_calls(self):
        """tool_search_call in response.completed keeps Codex in the tool loop."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()
        item = {
            "type": "tool_search_call",
            "id": "tsc_123",
            "call_id": "call_123",
            "status": "completed",
            "execution": "client",
            "arguments": {"query": "multi-agent", "limit": 8},
        }
        event = {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [item],
            },
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        finish_events = [e for e in events if e["type"] == "finish"]
        assert finish_events[0]["finish_reason"]["reason"] == "tool_calls"
        assert ctx.passthrough_output_items == [item]

    def test_response_completed_with_reasoning_does_not_set_tool_calls(self):
        """reasoning output items are preserved without entering tool loop."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()
        item = {
            "type": "reasoning",
            "id": "rs_123",
            "summary": [],
            "encrypted_content": "encrypted",
        }
        event = {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [item],
            },
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        finish_events = [e for e in events if e["type"] == "finish"]
        assert finish_events[0]["finish_reason"]["reason"] == "stop"
        assert ctx.passthrough_output_items == [item]

    def test_response_failed_emits_stream_end(self):
        """response.failed with context emits StreamEndEvent after FinishEvent."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()
        event = {
            "type": "response.failed",
            "response": {
                "status": "failed",
                "error": {"message": "Something went wrong"},
            },
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        types = [e["type"] for e in events]
        assert types == ["finish", "stream_end"]
        assert events[0]["finish_reason"]["reason"] == "error"
        assert ctx.is_ended is True

    def test_output_item_added_function_call_registers_tool(self):
        """response.output_item.added (function_call) registers tool in context."""
        ctx = OpenAIResponsesStreamContext()
        event = {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {
                "type": "function_call",
                "call_id": "call_abc",
                "name": "get_weather",
                "arguments": "",
            },
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        assert len(events) == 1
        assert events[0]["type"] == "tool_call_start"
        assert ctx.get_tool_name("call_abc") == "get_weather"

    def test_output_item_added_message_emits_passthrough_with_context(self):
        """response.output_item.added (message) preserves metadata with context.

        The actual content block is signaled by response.content_part.added.
        """
        ctx = OpenAIResponsesStreamContext()
        event = {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [],
            },
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        assert len(events) == 1
        assert events[0]["type"] == "provider_passthrough"
        assert ctx.message_item_metadata["type"] == "message"
        assert ctx.message_item_metadata["role"] == "assistant"

    def test_output_item_added_message_without_context_no_events(self):
        """response.output_item.added (message) without context produces no events."""
        event = {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [],
            },
        }
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    def test_output_item_added_tool_search_passthrough(self):
        """tool_search_call output items round-trip as provider passthrough."""
        ctx_from = OpenAIResponsesStreamContext()
        ctx_to = OpenAIResponsesStreamContext()
        event = {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "tool_search_call",
                "id": "tsc_123",
                "call_id": "call_123",
                "status": "completed",
                "execution": "client",
                "arguments": {
                    "query": "multi-agent subagent spawn",
                    "limit": 8,
                },
            },
        }

        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx_from),
        )

        assert len(events) == 1
        assert events[0]["type"] == "provider_passthrough"
        assert events[0]["provider"] == "openai_responses"
        assert events[0]["payload"] == event
        assert ctx_from.passthrough_output_items == [event["item"]]

        restored = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(events[0], context=ctx_to),
        )
        assert restored["type"] == "response.output_item.added"
        assert restored["item"]["type"] == "tool_search_call"
        assert restored["item"]["call_id"] == "call_123"

    def test_content_part_added_emits_content_block_start(self):
        """response.content_part.added with context emits ContentBlockStartEvent."""
        ctx = OpenAIResponsesStreamContext()
        event = {
            "type": "response.content_part.added",
            "part": {"type": "output_text", "text": ""},
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        assert len(events) == 1
        assert events[0]["type"] == "content_block_start"
        assert events[0]["block_type"] == "text"
        assert events[0]["block_index"] == 0

    def test_content_part_added_summary_text(self):
        """response.content_part.added with summary_text maps to thinking block type."""
        ctx = OpenAIResponsesStreamContext()
        event = {
            "type": "response.content_part.added",
            "part": {"type": "summary_text", "text": ""},
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        assert len(events) == 1
        assert events[0]["block_type"] == "thinking"

    def test_content_part_added_without_context_no_events(self):
        """response.content_part.added without context produces no events."""
        event = {
            "type": "response.content_part.added",
            "part": {"type": "output_text", "text": ""},
        }
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    def test_content_part_done_emits_content_block_end(self):
        """response.content_part.done with context emits ContentBlockEndEvent."""
        ctx = OpenAIResponsesStreamContext()
        ctx.next_block_index()  # set to 0
        event = {
            "type": "response.content_part.done",
            "part": {"type": "output_text", "text": "Hello"},
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        assert len(events) == 1
        assert events[0]["type"] == "content_block_end"
        assert events[0]["block_index"] == 0

    def test_content_part_done_without_context_no_events(self):
        """response.content_part.done without context produces no events."""
        event = {
            "type": "response.content_part.done",
            "part": {"type": "output_text", "text": "Hello"},
        }
        events = self.converter.stream_response_from_provider(event)
        assert events == []

    def test_output_item_done_message_no_events(self):
        """response.output_item.done (message) with context produces no IR events.

        The actual content block end is signaled by response.content_part.done.
        """
        ctx = OpenAIResponsesStreamContext()
        ctx.next_block_index()  # set to 0
        event = {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hello"}],
            },
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        assert len(events) == 0

    def test_text_delta_unchanged_with_context(self):
        """Text delta behavior is unchanged when context is provided."""
        ctx = OpenAIResponsesStreamContext()
        event = {
            "type": "response.output_text.delta",
            "delta": "Hello",
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        assert len(events) == 1
        assert events[0]["type"] == "text_delta"
        assert events[0]["text"] == "Hello"

    def test_response_completed_without_context_no_stream_end(self):
        """response.completed without context does not emit StreamEndEvent."""
        event = {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [],
            },
        }
        events = cast(list[Any], self.converter.stream_response_from_provider(event))
        types = [e["type"] for e in events]
        assert "stream_end" not in types
        assert "finish" in types


class TestStreamResponseToProviderWithContext:
    """Tests for stream_response_to_provider with StreamContext."""

    def setup_method(self):
        self.converter = OpenAIResponsesConverter()

    def test_stream_start_event(self):
        """StreamStartEvent → response.created."""
        ctx = OpenAIResponsesStreamContext()
        event = cast(
            StreamStartEvent,
            {
                "type": "stream_start",
                "response_id": "resp_abc123",
                "model": "gpt-4o",
            },
        )
        result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(event, context=ctx),
        )
        assert result["type"] == "response.created"
        assert result["response"]["id"] == "resp_abc123"
        assert result["response"]["model"] == "gpt-4o"
        assert result["response"]["status"] == "in_progress"
        assert result["response"]["output"] == []
        assert ctx.response_id == "resp_abc123"
        assert ctx.model == "gpt-4o"
        assert ctx.is_started is True

    def test_stream_start_without_context(self):
        """StreamStartEvent without context still produces response.created."""
        event = cast(
            StreamStartEvent,
            {
                "type": "stream_start",
                "response_id": "resp_abc123",
                "model": "gpt-4o",
            },
        )
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result["type"] == "response.created"
        assert result["response"]["id"] == "resp_abc123"

    def test_stream_end_event(self):
        """StreamEndEvent → empty dict."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()
        event = cast(StreamEndEvent, {"type": "stream_end"})
        result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(event, context=ctx),
        )
        assert result == {}
        assert ctx.is_ended is True

    def test_stream_end_without_context(self):
        """StreamEndEvent without context → empty dict."""
        event = cast(StreamEndEvent, {"type": "stream_end"})
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result == {}

    def test_content_block_start_text(self):
        """ContentBlockStartEvent (text) → response.content_part.added."""
        event = cast(
            ContentBlockStartEvent,
            {
                "type": "content_block_start",
                "block_index": 0,
                "block_type": "text",
            },
        )
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result["type"] == "response.content_part.added"
        assert result["part"]["type"] == "output_text"
        assert result["part"]["text"] == ""

    def test_content_block_start_non_text(self):
        """ContentBlockStartEvent (non-text) → empty dict."""
        event = cast(
            ContentBlockStartEvent,
            {
                "type": "content_block_start",
                "block_index": 0,
                "block_type": "thinking",
            },
        )
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result == {}

    def test_content_block_end(self):
        """ContentBlockEndEvent → response.content_part.done."""
        event = cast(
            ContentBlockEndEvent,
            {
                "type": "content_block_end",
                "block_index": 0,
            },
        )
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result["type"] == "response.content_part.done"
        assert result["part"]["type"] == "output_text"

    def test_usage_with_context_no_duplicate_completed(self):
        """UsageEvent with context stores usage, returns empty dict (no duplicate)."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()
        event = cast(
            UsageEvent,
            {
                "type": "usage",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )
        result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(event, context=ctx),
        )
        assert result == {}
        assert ctx.pending_usage is not None
        assert ctx.pending_usage["prompt_tokens"] == 10
        assert ctx.pending_usage["completion_tokens"] == 5

    def test_usage_without_context_backward_compat(self):
        """UsageEvent without context produces response.completed (backward compat)."""
        event = cast(
            UsageEvent,
            {
                "type": "usage",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )
        result = cast(dict[str, Any], self.converter.stream_response_to_provider(event))
        assert result["type"] == "response.completed"
        assert result["response"]["usage"]["input_tokens"] == 10

    def test_finish_with_context_defers_response_completed(self):
        """FinishEvent with context defers response.completed to StreamEndEvent."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()
        ctx.pending_usage = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
        event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "stop"}},
        )
        results = cast(
            list[dict[str, Any]],
            self.converter.stream_response_to_provider(event, context=ctx),
        )
        # FinishEvent should NOT emit response.completed with context
        assert not any(r.get("type") == "response.completed" for r in results)
        # But it should store the response in context for later
        assert ctx.pending_response is not None
        assert ctx.pending_response["usage"]["input_tokens"] == 10

        # StreamEndEvent emits the deferred response.completed
        end_result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(
                cast(StreamEndEvent, {"type": "stream_end"}),
                context=ctx,
            ),
        )
        assert end_result["type"] == "response.completed"
        assert end_result["response"]["status"] == "completed"
        assert end_result["response"]["usage"]["input_tokens"] == 10
        assert end_result["response"]["usage"]["output_tokens"] == 5
        assert end_result["response"]["usage"]["total_tokens"] == 15

    def test_finish_with_context_no_pending_usage(self):
        """FinishEvent with context but no pending usage omits usage field."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()
        event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "stop"}},
        )
        results = cast(
            list[dict[str, Any]],
            self.converter.stream_response_to_provider(event, context=ctx),
        )
        # FinishEvent defers response.completed
        assert not any(r.get("type") == "response.completed" for r in results)
        assert ctx.pending_response is not None
        assert "usage" not in ctx.pending_response

        # StreamEndEvent emits without usage
        end_result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(
                cast(StreamEndEvent, {"type": "stream_end"}),
                context=ctx,
            ),
        )
        assert end_result["type"] == "response.completed"
        assert end_result["response"]["status"] == "completed"
        assert "usage" not in end_result["response"]

    def test_finish_without_context_backward_compat(self):
        """FinishEvent without context produces response.completed (backward compat)."""
        event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "stop"}},
        )
        results = cast(
            list[dict[str, Any]], self.converter.stream_response_to_provider(event)
        )
        completed = next(r for r in results if r["type"] == "response.completed")
        assert completed["response"]["status"] == "completed"

    def test_no_duplicate_response_completed_with_context(self):
        """With context, UsageEvent + FinishEvent + StreamEndEvent produce one response.completed."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()

        # First: UsageEvent → stored in context, returns empty
        usage_event = cast(
            UsageEvent,
            {
                "type": "usage",
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            },
        )
        usage_result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(usage_event, context=ctx),
        )
        assert usage_result == {}

        # Second: FinishEvent → deferred, no response.completed yet
        finish_event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "stop"}},
        )
        finish_results = cast(
            list[dict[str, Any]],
            self.converter.stream_response_to_provider(finish_event, context=ctx),
        )
        assert not any(r.get("type") == "response.completed" for r in finish_results)
        assert ctx.pending_response is not None
        assert ctx.pending_response["usage"]["input_tokens"] == 20

        # Third: StreamEndEvent → emits deferred response.completed
        end_result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(
                cast(StreamEndEvent, {"type": "stream_end"}),
                context=ctx,
            ),
        )
        assert end_result["type"] == "response.completed"
        assert end_result["response"]["usage"]["input_tokens"] == 20
        assert end_result["response"]["usage"]["output_tokens"] == 10

    def test_full_stream_sequence_with_context(self):
        """Full stream sequence produces correct events with no duplicates."""
        ctx = OpenAIResponsesStreamContext()

        # 1. StreamStartEvent
        start_result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(
                cast(
                    StreamStartEvent,
                    {
                        "type": "stream_start",
                        "response_id": "resp_123",
                        "model": "gpt-4o",
                    },
                ),
                context=ctx,
            ),
        )
        assert start_result["type"] == "response.created"

        # 2. ContentBlockStartEvent — with context, first text block emits
        # both output_item.added and content_part.added as a list.
        block_start_results = self.converter.stream_response_to_provider(
            cast(
                ContentBlockStartEvent,
                {
                    "type": "content_block_start",
                    "block_index": 0,
                    "block_type": "text",
                },
            ),
            context=ctx,
        )
        assert isinstance(block_start_results, list)
        assert len(block_start_results) == 2
        assert block_start_results[0]["type"] == "response.output_item.added"
        assert block_start_results[1]["type"] == "response.content_part.added"

        # 3. TextDeltaEvent — output_item already emitted by ContentBlockStart,
        #    so this should return a single delta (not a list).
        text_results = self.converter.stream_response_to_provider(
            cast(TextDeltaEvent, {"type": "text_delta", "text": "Hello"}),
            context=ctx,
        )
        if isinstance(text_results, list):
            text_delta = next(
                r for r in text_results if r["type"] == "response.output_text.delta"
            )
        else:
            text_delta = text_results
        assert text_delta["type"] == "response.output_text.delta"

        # 4. ContentBlockEndEvent — returns output_text.done + content_part.done
        block_end_results = self.converter.stream_response_to_provider(
            cast(
                ContentBlockEndEvent,
                {"type": "content_block_end", "block_index": 0},
            ),
            context=ctx,
        )
        assert isinstance(block_end_results, list)
        assert len(block_end_results) == 2
        assert block_end_results[0]["type"] == "response.output_text.done"
        assert block_end_results[1]["type"] == "response.content_part.done"

        # 5. UsageEvent → stored, no output
        usage_result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(
                cast(
                    UsageEvent,
                    {
                        "type": "usage",
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                        },
                    },
                ),
                context=ctx,
            ),
        )
        assert usage_result == {}

        # 6. FinishEvent → deferred, no response.completed yet
        finish_results = cast(
            list[dict[str, Any]],
            self.converter.stream_response_to_provider(
                cast(
                    FinishEvent,
                    {"type": "finish", "finish_reason": {"reason": "stop"}},
                ),
                context=ctx,
            ),
        )
        assert not any(r.get("type") == "response.completed" for r in finish_results)
        assert ctx.pending_response is not None

        # 7. StreamEndEvent → emits deferred response.completed
        end_result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(
                cast(StreamEndEvent, {"type": "stream_end"}),
                context=ctx,
            ),
        )
        assert end_result["type"] == "response.completed"
        assert end_result["response"]["usage"]["input_tokens"] == 10

        # Verify: only ONE response.completed was produced in the entire sequence
        all_results: list[Any] = [
            start_result,
            usage_result,
            end_result,
        ]
        # Flatten list results
        if isinstance(block_start_results, list):
            all_results.extend(block_start_results)
        else:
            all_results.append(block_start_results)
        if isinstance(block_end_results, list):
            all_results.extend(block_end_results)
        else:
            all_results.append(block_end_results)
        if isinstance(text_results, list):
            all_results.extend(text_results)
        else:
            all_results.append(text_results)
        all_results.extend(finish_results)
        completed_count = sum(
            1
            for r in all_results
            if isinstance(r, dict) and r.get("type") == "response.completed"
        )
        assert completed_count == 1

    def test_cross_chunk_usage_after_finish(self):
        """UsageEvent arriving after FinishEvent (OpenAI Chat pattern) is merged."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()

        # 1. FinishEvent arrives first (no pending_usage yet)
        finish_event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "stop"}},
        )
        finish_results = cast(
            list[dict[str, Any]],
            self.converter.stream_response_to_provider(finish_event, context=ctx),
        )
        assert not any(r.get("type") == "response.completed" for r in finish_results)
        assert ctx.pending_response is not None
        assert "usage" not in ctx.pending_response

        # 2. UsageEvent arrives in a separate chunk
        usage_event = cast(
            UsageEvent,
            {
                "type": "usage",
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 25,
                    "total_tokens": 75,
                },
            },
        )
        usage_result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(usage_event, context=ctx),
        )
        assert usage_result == {}

        # 3. StreamEndEvent merges the late usage into deferred response
        end_result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(
                cast(StreamEndEvent, {"type": "stream_end"}),
                context=ctx,
            ),
        )
        assert end_result["type"] == "response.completed"
        assert end_result["response"]["usage"]["input_tokens"] == 50
        assert end_result["response"]["usage"]["output_tokens"] == 25
        assert end_result["response"]["usage"]["total_tokens"] == 75


class TestCustomToolCallStreaming:
    """Tests for custom_tool_call streaming events."""

    def setup_method(self):
        self.converter = OpenAIResponsesConverter()

    # --- From provider ---

    def test_custom_tool_call_output_item_added(self):
        """response.output_item.added with custom_tool_call produces ToolCallStartEvent."""
        ctx = OpenAIResponsesStreamContext()
        event = {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "ctc_001",
                "type": "custom_tool_call",
                "call_id": "call_custom_1",
                "name": "my_tool",
                "input": "",
            },
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        assert len(events) == 1
        assert events[0]["type"] == "tool_call_start"
        assert events[0]["tool_call_id"] == "call_custom_1"
        assert events[0]["tool_name"] == "my_tool"
        assert events[0]["tool_type"] == "custom"
        assert events[0]["tool_call_index"] == 0
        # Context should register the custom tool type
        assert ctx.get_tool_name("call_custom_1") == "my_tool"
        assert ctx.get_tool_type("call_custom_1") == "custom"
        assert ctx.get_tool_call_item_id("call_custom_1") == "ctc_001"

    def test_custom_tool_call_input_delta(self):
        """response.custom_tool_call_input.delta produces ToolCallDeltaEvent."""
        ctx = OpenAIResponsesStreamContext()
        ctx.register_tool_call("call_custom_1", "my_tool", "custom")
        event = {
            "type": "response.custom_tool_call_input.delta",
            "call_id": "call_custom_1",
            "delta": "hello ",
            "output_index": 0,
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        assert len(events) == 1
        assert events[0]["type"] == "tool_call_delta"
        assert events[0]["tool_call_id"] == "call_custom_1"
        assert events[0]["arguments_delta"] == "hello "
        assert ctx.get_tool_call_args("call_custom_1") == "hello "

    def test_custom_tool_call_input_done(self):
        """response.custom_tool_call_input.done stores final input in context."""
        ctx = OpenAIResponsesStreamContext()
        ctx.register_tool_call("call_custom_1", "my_tool", "custom")
        ctx.append_tool_call_args("call_custom_1", "hello ")
        event = {
            "type": "response.custom_tool_call_input.done",
            "call_id": "call_custom_1",
            "input": "hello world",
        }
        events = cast(
            list[Any],
            self.converter.stream_response_from_provider(event, context=ctx),
        )
        # Done event produces no IR events
        assert len(events) == 0
        # But the final input is stored in context
        assert ctx.get_tool_call_args("call_custom_1") == "hello world"

    def test_custom_tool_call_output_item_done(self):
        """response.output_item.done with custom_tool_call stores input in context."""
        ctx = OpenAIResponsesStreamContext()
        ctx.register_tool_call("call_custom_1", "my_tool", "custom")
        event = {
            "type": "response.output_item.done",
            "item": {
                "type": "custom_tool_call",
                "call_id": "call_custom_1",
                "name": "my_tool",
                "input": "final input text",
            },
        }
        self.converter.stream_response_from_provider(event, context=ctx)
        assert ctx.get_tool_call_args("call_custom_1") == "final input text"

    # --- To provider ---

    def test_tool_call_start_custom_to_p(self):
        """ToolCallStartEvent with tool_type='custom' → custom_tool_call item."""
        ctx = OpenAIResponsesStreamContext()
        event = cast(
            ToolCallStartEvent,
            {
                "type": "tool_call_start",
                "tool_call_id": "call_custom_1",
                "tool_name": "my_tool",
                "tool_type": "custom",
                "tool_call_index": 0,
            },
        )
        result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(event, context=ctx),
        )
        assert result["type"] == "response.output_item.added"
        assert result["item"]["type"] == "custom_tool_call"
        assert result["item"]["call_id"] == "call_custom_1"
        assert result["item"]["name"] == "my_tool"
        assert result["item"]["input"] == ""
        assert result["item"]["status"] == "in_progress"
        # Context should have registered the custom type
        assert ctx.get_tool_type("call_custom_1") == "custom"

    def test_tool_call_start_restores_custom_type_from_request_context(self):
        """A bridged Chat call restores Code Mode ``exec`` to custom format."""
        ctx = OpenAIResponsesStreamContext()
        ctx.store_responses_native_tool_type_map({"exec": "custom"})
        event = cast(
            ToolCallStartEvent,
            {
                "type": "tool_call_start",
                "tool_call_id": "call_exec",
                "tool_name": "exec",
                "tool_type": "function",
                "tool_call_index": 0,
            },
        )

        added = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(event, context=ctx),
        )
        assert added["item"]["type"] == "custom_tool_call"
        assert added["item"]["input"] == ""
        assert ctx.get_tool_type("call_exec") == "custom"

        delta = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(
                cast(
                    ToolCallDeltaEvent,
                    {
                        "type": "tool_call_delta",
                        "tool_call_id": "call_exec",
                        "arguments_delta": '{"cmd":"head -n 1 README.md"}',
                        "tool_call_index": 0,
                    },
                ),
                context=ctx,
            ),
        )
        assert delta["type"] == "response.custom_tool_call_input.delta"

        completed = cast(
            list[dict[str, Any]],
            self.converter.stream_response_to_provider(
                cast(
                    FinishEvent,
                    {
                        "type": "finish",
                        "finish_reason": {"reason": "tool_calls"},
                    },
                ),
                context=ctx,
            ),
        )
        custom_done = next(
            item
            for item in completed
            if item["type"] == "response.custom_tool_call_input.done"
        )
        assert custom_done["input"] == '{"cmd":"head -n 1 README.md"}'
        output_done = next(
            item for item in completed if item["type"] == "response.output_item.done"
        )
        assert output_done["item"]["type"] == "custom_tool_call"
        assert output_done["item"]["input"] == '{"cmd":"head -n 1 README.md"}'

    def test_tool_call_delta_custom_to_p(self):
        """ToolCallDeltaEvent for custom tool → custom_tool_call_input.delta."""
        ctx = OpenAIResponsesStreamContext()
        ctx.register_tool_call("call_custom_1", "my_tool", "custom")
        ctx.register_tool_call_item("call_custom_1", "call_custom_1")
        event = cast(
            ToolCallDeltaEvent,
            {
                "type": "tool_call_delta",
                "tool_call_id": "call_custom_1",
                "arguments_delta": "hello ",
                "tool_call_index": 0,
            },
        )
        result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(event, context=ctx),
        )
        assert result["type"] == "response.custom_tool_call_input.delta"
        assert result["delta"] == "hello "

    def test_tool_call_delta_function_to_p(self):
        """ToolCallDeltaEvent for function tool → function_call_arguments.delta."""
        ctx = OpenAIResponsesStreamContext()
        ctx.register_tool_call("call_fn_1", "get_weather", "function")
        ctx.register_tool_call_item("call_fn_1", "call_fn_1")
        event = cast(
            ToolCallDeltaEvent,
            {
                "type": "tool_call_delta",
                "tool_call_id": "call_fn_1",
                "arguments_delta": '{"city":',
            },
        )
        result = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(event, context=ctx),
        )
        assert result["type"] == "response.function_call_arguments.delta"

    def test_finish_with_custom_tool_call(self):
        """FinishEvent emits custom_tool_call done events and output items."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()
        ctx.register_tool_call("call_custom_1", "my_tool", "custom")
        ctx.register_tool_call_item("call_custom_1", "ctc_001")
        ctx.set_tool_call_args("call_custom_1", "hello world")

        event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "tool_calls"}},
        )
        results = cast(
            list[dict[str, Any]],
            self.converter.stream_response_to_provider(event, context=ctx),
        )

        # Should have custom_tool_call_input.done and output_item.done
        done_events = [
            r
            for r in results
            if r.get("type") == "response.custom_tool_call_input.done"
        ]
        assert len(done_events) == 1
        assert done_events[0]["input"] == "hello world"
        assert done_events[0]["item_id"] == "ctc_001"

        item_done_events = [
            r for r in results if r.get("type") == "response.output_item.done"
        ]
        assert len(item_done_events) == 1
        assert item_done_events[0]["item"]["type"] == "custom_tool_call"
        assert item_done_events[0]["item"]["input"] == "hello world"
        assert item_done_events[0]["item"]["name"] == "my_tool"
        assert item_done_events[0]["item"]["status"] == "completed"

        # Deferred response should have custom_tool_call output
        assert ctx.pending_response is not None
        output = ctx.pending_response["output"]
        assert len(output) == 1
        assert output[0]["type"] == "custom_tool_call"
        assert output[0]["input"] == "hello world"

    def test_finish_with_mixed_tool_calls(self):
        """FinishEvent with both function and custom tool calls emits correct types."""
        ctx = OpenAIResponsesStreamContext()
        ctx.mark_started()
        ctx.register_tool_call("call_fn_1", "get_weather", "function")
        ctx.register_tool_call_item("call_fn_1", "fc_001")
        ctx.set_tool_call_args("call_fn_1", '{"city": "NYC"}')
        ctx.register_tool_call("call_custom_1", "my_tool", "custom")
        ctx.register_tool_call_item("call_custom_1", "ctc_001")
        ctx.set_tool_call_args("call_custom_1", "do something")

        event = cast(
            FinishEvent,
            {"type": "finish", "finish_reason": {"reason": "tool_calls"}},
        )
        results = cast(
            list[dict[str, Any]],
            self.converter.stream_response_to_provider(event, context=ctx),
        )

        # Function call done events
        fn_done = [
            r
            for r in results
            if r.get("type") == "response.function_call_arguments.done"
        ]
        assert len(fn_done) == 1
        assert fn_done[0]["arguments"] == '{"city": "NYC"}'

        # Custom tool call done events
        custom_done = [
            r
            for r in results
            if r.get("type") == "response.custom_tool_call_input.done"
        ]
        assert len(custom_done) == 1
        assert custom_done[0]["input"] == "do something"

        # Output items
        item_done = [r for r in results if r.get("type") == "response.output_item.done"]
        assert len(item_done) == 2
        assert item_done[0]["item"]["type"] == "function_call"
        assert item_done[1]["item"]["type"] == "custom_tool_call"

    # --- Round-trip ---

    def test_custom_tool_call_stream_round_trip(self):
        """Custom tool call round-trips through streaming: provider → IR → provider."""
        ctx_from = OpenAIResponsesStreamContext()
        ctx_to = OpenAIResponsesStreamContext()

        # 1. output_item.added
        added_event = {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "ctc_001",
                "type": "custom_tool_call",
                "call_id": "call_custom_1",
                "name": "my_tool",
                "input": "",
            },
        }
        ir_events = cast(
            list[Any],
            self.converter.stream_response_from_provider(added_event, context=ctx_from),
        )
        assert len(ir_events) == 1
        restored = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(ir_events[0], context=ctx_to),
        )
        assert restored["item"]["type"] == "custom_tool_call"
        assert restored["item"]["name"] == "my_tool"
        assert restored["item"]["input"] == ""

        # 2. custom_tool_call_input.delta
        delta_event = {
            "type": "response.custom_tool_call_input.delta",
            "call_id": "call_custom_1",
            "delta": "search query",
            "output_index": 0,
        }
        ir_events = cast(
            list[Any],
            self.converter.stream_response_from_provider(delta_event, context=ctx_from),
        )
        assert len(ir_events) == 1
        restored = cast(
            dict[str, Any],
            self.converter.stream_response_to_provider(ir_events[0], context=ctx_to),
        )
        assert restored["type"] == "response.custom_tool_call_input.delta"
        assert restored["delta"] == "search query"

    def test_tool_search_call_stream_round_trip_completed_output(self):
        """tool_search_call survives streaming provider → IR → provider conversion."""
        ctx_from = OpenAIResponsesStreamContext()
        ctx_to = OpenAIResponsesStreamContext()

        tool_search_item = {
            "type": "tool_search_call",
            "id": "tsc_123",
            "call_id": "call_123",
            "status": "completed",
            "execution": "client",
            "arguments": {"query": "multi-agent", "limit": 8},
        }
        chunks = [
            {
                "type": "response.created",
                "response": {
                    "id": "resp_123",
                    "object": "response",
                    "model": "gpt-5.5",
                    "created_at": 123,
                    "status": "in_progress",
                    "output": [],
                },
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": tool_search_item,
            },
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_123",
                    "object": "response",
                    "model": "gpt-5.5",
                    "created_at": 123,
                    "status": "completed",
                    "output": [tool_search_item],
                },
            },
        ]

        restored_events: list[dict[str, Any]] = []
        for chunk in chunks:
            ir_events = cast(
                list[Any],
                self.converter.stream_response_from_provider(chunk, context=ctx_from),
            )
            for ir_event in ir_events:
                restored = self.converter.stream_response_to_provider(
                    ir_event, context=ctx_to
                )
                if isinstance(restored, list):
                    restored_events.extend(cast(list[dict[str, Any]], restored))
                elif restored:
                    restored_events.append(cast(dict[str, Any], restored))

        completed = [
            event
            for event in restored_events
            if event.get("type") == "response.completed"
        ]
        assert completed
        assert completed[-1]["response"]["output"] == [tool_search_item]

    def test_chat_tool_search_stream_restores_native_responses_item(self):
        """Chat tool_search stream events serialize as Responses tool_search_call."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "find github tools",
                "stream": True,
                "tools": [
                    {
                        "type": "tool_search",
                        "description": "Search for loadable tools.",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    }
                ],
            }
        )
        processor = pipeline.create_stream_processor(finalize_on_finish_eof=True)

        source_events: list[dict[str, Any]] = []
        for chunk in [
            {
                "id": "chatcmpl-tool-search",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_search",
                                    "type": "function",
                                    "function": {
                                        "name": "tool_search",
                                        "arguments": '{"query":',
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-tool-search",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '"github"}'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-tool-search",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "deepseek-v4-flash",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]:
            source_events.extend(processor.process_chunk(chunk))
        source_events.extend(processor.finalize_stream())

        added = [
            event
            for event in source_events
            if event.get("type") == "response.output_item.added"
        ]
        assert added[-1]["item"]["type"] == "tool_search_call"
        assert added[-1]["item"]["id"] == "tsc_search"
        assert not any(
            event.get("type") == "response.function_call_arguments.delta"
            for event in source_events
        )
        completed = [
            event
            for event in source_events
            if event.get("type") == "response.completed"
        ]
        output = completed[-1]["response"]["output"]
        assert output[-1]["type"] == "tool_search_call"
        assert output[-1]["arguments"] == {"query": "github"}

    def test_reasoning_stream_round_trip_completed_output(self):
        """reasoning output items survive streaming provider → IR → provider conversion."""
        ctx_from = OpenAIResponsesStreamContext()
        ctx_to = OpenAIResponsesStreamContext()

        reasoning_item = {
            "type": "reasoning",
            "id": "rs_123",
            "summary": [],
            "encrypted_content": "encrypted",
        }
        chunks = [
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": reasoning_item,
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": reasoning_item,
            },
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_123",
                    "object": "response",
                    "model": "gpt-5.5",
                    "created_at": 123,
                    "status": "completed",
                    "output": [reasoning_item],
                },
            },
        ]

        restored_events: list[dict[str, Any]] = []
        for chunk in chunks:
            ir_events = cast(
                list[Any],
                self.converter.stream_response_from_provider(chunk, context=ctx_from),
            )
            for ir_event in ir_events:
                restored = self.converter.stream_response_to_provider(
                    ir_event, context=ctx_to
                )
                if isinstance(restored, list):
                    restored_events.extend(cast(list[dict[str, Any]], restored))
                elif restored:
                    restored_events.append(cast(dict[str, Any], restored))

        added = [
            event
            for event in restored_events
            if event.get("type") == "response.output_item.added"
        ]
        assert added
        assert added[0]["item"] == reasoning_item

        done = [
            event
            for event in restored_events
            if event.get("type") == "response.output_item.done"
        ]
        assert done
        assert done[-1]["item"] == reasoning_item

        completed = [
            event
            for event in restored_events
            if event.get("type") == "response.completed"
        ]
        assert completed
        assert completed[-1]["response"]["output"] == [reasoning_item]

    def test_chat_reasoning_tool_call_survives_next_chat_request(self):
        """Chat reasoning_content is preserved through Responses history."""
        chat = OpenAIChatConverter()
        responses = OpenAIResponsesConverter()
        responses_ctx = OpenAIResponsesStreamContext()

        chat_chunks = [
            {
                "id": "chatcmpl_123",
                "model": "deepseek-reasoner",
                "created": 123,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"reasoning_content": ""},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"reasoning_content": "I need a tool."},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "index": 0,
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"city":"NYC"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        ]

        downstream_events: list[dict[str, Any]] = []
        for chunk in chat_chunks:
            ir_events = cast(list[Any], chat.stream_response_from_provider(chunk))
            for ir_event in ir_events:
                downstream = responses.stream_response_to_provider(
                    ir_event, context=responses_ctx
                )
                if isinstance(downstream, list):
                    downstream_events.extend(downstream)
                elif downstream:
                    downstream_events.append(cast(dict[str, Any], downstream))

        assert responses_ctx.pending_response is not None
        response_output: list[dict[str, Any]] = []
        for event in downstream_events:
            if event.get("type") in (
                "response.output_item.added",
                "response.output_item.done",
            ):
                item = event.get("item")
                if isinstance(item, dict):
                    response_output = [
                        existing
                        for existing in response_output
                        if existing.get("id") != item.get("id")
                    ]
                    response_output.append(item)
        assert response_output[0]["type"] == "reasoning"

        next_responses_request = {
            "model": "deepseek-reasoner",
            "input": [
                {"type": "message", "role": "user", "content": "weather?"},
                *response_output,
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "72F",
                },
            ],
        }
        ir_request = responses.request_from_provider(next_responses_request)
        chat_request, warnings = chat.request_to_provider(ir_request)

        assert warnings == []
        assistant_message = next(
            msg for msg in chat_request["messages"] if msg["role"] == "assistant"
        )
        assert assistant_message["reasoning_content"] == "I need a tool."
        assert assistant_message["tool_calls"][0]["id"] == "call_1"

    def test_message_phase_stream_round_trip(self):
        """Responses message item metadata survives streaming round-trip."""
        ctx_from = OpenAIResponsesStreamContext()
        ctx_to = OpenAIResponsesStreamContext()

        chunks = [
            {
                "type": "response.created",
                "response": {
                    "id": "resp_123",
                    "object": "response",
                    "model": "gpt-5.5",
                    "created_at": 123,
                    "status": "in_progress",
                    "output": [],
                },
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "status": "in_progress",
                    "phase": "commentary",
                    "content": [],
                },
            },
            {
                "type": "response.content_part.added",
                "item_id": "msg_123",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": ""},
            },
            {
                "type": "response.output_text.delta",
                "item_id": "msg_123",
                "output_index": 0,
                "content_index": 0,
                "delta": "working",
            },
            {
                "type": "response.content_part.done",
                "item_id": "msg_123",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "working"},
            },
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_123",
                    "object": "response",
                    "model": "gpt-5.5",
                    "created_at": 123,
                    "status": "completed",
                    "output": [
                        {
                            "id": "msg_123",
                            "type": "message",
                            "role": "assistant",
                            "phase": "commentary",
                            "content": [{"type": "output_text", "text": "working"}],
                        }
                    ],
                },
            },
        ]

        restored_events: list[dict[str, Any]] = []
        for chunk in chunks:
            ir_events = cast(
                list[Any],
                self.converter.stream_response_from_provider(chunk, context=ctx_from),
            )
            for ir_event in ir_events:
                restored = self.converter.stream_response_to_provider(
                    ir_event, context=ctx_to
                )
                if isinstance(restored, list):
                    restored_events.extend(cast(list[dict[str, Any]], restored))
                elif restored:
                    restored_events.append(cast(dict[str, Any], restored))

        added = [
            event
            for event in restored_events
            if event.get("type") == "response.output_item.added"
        ]
        assert added
        assert added[0]["item"]["id"] == "msg_123"
        assert added[0]["item"]["phase"] == "commentary"

        done = [
            event
            for event in restored_events
            if event.get("type") == "response.output_item.done"
            and event.get("item", {}).get("type") == "message"
        ]
        assert done
        assert done[0]["item"]["id"] == "msg_123"
        assert done[0]["item"]["phase"] == "commentary"
        assert done[0]["item"]["status"] == "completed"

        completed = [
            event
            for event in restored_events
            if event.get("type") == "response.completed"
        ]
        assert completed
        output = completed[-1]["response"]["output"]
        assert output[0]["id"] == "msg_123"
        assert output[0]["phase"] == "commentary"
        assert output[0]["content"] == [{"type": "output_text", "text": "working"}]

    def test_namespaced_function_call_stream_round_trip(self):
        """Responses namespaced function calls keep namespace and item id."""
        ctx_from = OpenAIResponsesStreamContext()
        ctx_to = OpenAIResponsesStreamContext()

        function_item = {
            "type": "function_call",
            "id": "fc_123",
            "call_id": "call_123",
            "name": "spawn_agent",
            "namespace": "multi_agent_v1",
            "arguments": "",
            "status": "in_progress",
        }
        chunks = [
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": function_item,
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_123",
                "output_index": 0,
                "delta": '{"agent_type":"default"}',
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    **function_item,
                    "arguments": '{"agent_type":"default"}',
                    "status": "completed",
                },
            },
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_123",
                    "object": "response",
                    "model": "gpt-5.5",
                    "created_at": 123,
                    "status": "completed",
                    "output": [
                        {
                            **function_item,
                            "arguments": '{"agent_type":"default"}',
                            "status": "completed",
                        }
                    ],
                },
            },
        ]

        restored_events: list[dict[str, Any]] = []
        for chunk in chunks:
            ir_events = cast(
                list[Any],
                self.converter.stream_response_from_provider(chunk, context=ctx_from),
            )
            for ir_event in ir_events:
                restored = self.converter.stream_response_to_provider(
                    ir_event, context=ctx_to
                )
                if isinstance(restored, list):
                    restored_events.extend(cast(list[dict[str, Any]], restored))
                elif restored:
                    restored_events.append(cast(dict[str, Any], restored))

        added = next(
            event
            for event in restored_events
            if event.get("type") == "response.output_item.added"
        )
        assert added["item"]["id"] == "fc_123"
        assert added["item"]["call_id"] == "call_123"
        assert added["item"]["namespace"] == "multi_agent_v1"

        done = [
            event
            for event in restored_events
            if event.get("type") == "response.output_item.done"
        ]
        assert done
        assert done[-1]["item"]["id"] == "fc_123"
        assert done[-1]["item"]["namespace"] == "multi_agent_v1"

        completed = [
            event
            for event in restored_events
            if event.get("type") == "response.completed"
        ]
        assert completed
        assert completed[-1]["response"]["output"][0]["id"] == "fc_123"
        assert completed[-1]["response"]["output"][0]["namespace"] == "multi_agent_v1"
