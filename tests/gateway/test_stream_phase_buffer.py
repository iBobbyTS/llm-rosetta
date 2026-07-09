"""Tests for full-turn Responses phase buffering."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codex_rosetta.converters.openai_responses._constants import ResponsesEventType
from codex_rosetta.gateway.proxy import _stream_event_generator, handle_streaming
from codex_rosetta.gateway.stream_phase_buffer import (
    COMMENTARY_PHASE,
    FINAL_ANSWER_PHASE,
    ResponsesPhaseBuffer,
)
from codex_rosetta.gateway.stream_trace import StreamTraceLogger
from codex_rosetta.routing import ResolvedRoute


def _buffer() -> ResponsesPhaseBuffer:
    return ResponsesPhaseBuffer(window_id="thread-1:0")


def _created() -> dict[str, Any]:
    return {
        "type": ResponsesEventType.RESPONSE_CREATED,
        "response": {"id": "resp_1", "status": "in_progress", "output": []},
    }


def _message_added() -> dict[str, Any]:
    return {
        "type": ResponsesEventType.OUTPUT_ITEM_ADDED,
        "output_index": 0,
        "item": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "in_progress",
            "content": [],
        },
    }


def _content_added() -> dict[str, Any]:
    return {
        "type": ResponsesEventType.CONTENT_PART_ADDED,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "part": {"type": "output_text", "text": ""},
    }


def _text_delta(text: str = "working") -> dict[str, Any]:
    return {
        "type": ResponsesEventType.OUTPUT_TEXT_DELTA,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "delta": text,
    }


def _text_done(text: str = "working") -> dict[str, Any]:
    return {
        "type": ResponsesEventType.OUTPUT_TEXT_DONE,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "text": text,
    }


def _content_done(text: str = "working") -> dict[str, Any]:
    return {
        "type": ResponsesEventType.CONTENT_PART_DONE,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "part": {"type": "output_text", "text": text},
    }


def _message_done(text: str = "working") -> dict[str, Any]:
    return {
        "type": ResponsesEventType.OUTPUT_ITEM_DONE,
        "output_index": 0,
        "item": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def _tool_added() -> dict[str, Any]:
    return {
        "type": ResponsesEventType.OUTPUT_ITEM_ADDED,
        "output_index": 1,
        "item": {
            "id": "call_1",
            "type": "function_call",
            "call_id": "call_1",
            "name": "exec_command",
            "arguments": "",
            "status": "in_progress",
        },
    }


def _tool_args_done() -> dict[str, Any]:
    return {
        "type": ResponsesEventType.FUNCTION_CALL_ARGS_DONE,
        "item_id": "call_1",
        "output_index": 1,
        "arguments": "{}",
    }


def _tool_done() -> dict[str, Any]:
    return {
        "type": ResponsesEventType.OUTPUT_ITEM_DONE,
        "output_index": 1,
        "item": {
            "id": "call_1",
            "type": "function_call",
            "call_id": "call_1",
            "name": "exec_command",
            "arguments": "{}",
            "status": "completed",
        },
    }


def _native_search_item(item_type: str) -> dict[str, Any]:
    if item_type == "tool_search_call":
        return {
            "id": "tsc_1",
            "type": "tool_search_call",
            "call_id": "call_search_1",
            "status": "completed",
            "execution": "client",
            "arguments": {"query": "github", "limit": 8},
        }
    if item_type == "web_search_call":
        return {
            "id": "wsc_1",
            "type": "web_search_call",
            "status": "completed",
            "action": {"type": "search", "query": "Codex compatibility"},
        }
    raise AssertionError(f"unsupported search item type: {item_type}")


def _native_search_event(item_type: str, event_type: str) -> dict[str, Any]:
    return {
        "type": event_type,
        "output_index": 1,
        "item": _native_search_item(item_type),
    }


def _completed(*, with_tool: bool) -> dict[str, Any]:
    output = [
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "working"}],
        }
    ]
    if with_tool:
        output.append(
            {
                "id": "call_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "exec_command",
                "arguments": "{}",
                "status": "completed",
            }
        )
    return {
        "type": ResponsesEventType.RESPONSE_COMPLETED,
        "response": {"id": "resp_1", "status": "completed", "output": output},
    }


def _completed_with_native_search(item_type: str) -> dict[str, Any]:
    event = _completed(with_tool=False)
    event["response"]["output"].append(_native_search_item(item_type))
    return event


def _failed() -> dict[str, Any]:
    return {
        "type": ResponsesEventType.RESPONSE_FAILED,
        "response": {
            "id": "resp_1",
            "status": "failed",
            "output": [
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "working"}],
                }
            ],
        },
    }


def _collect(
    buffer: ResponsesPhaseBuffer, events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    emitted: list[dict[str, Any]] = []
    for event in events:
        emitted.extend(buffer.process(event))
    emitted.extend(buffer.flush())
    return emitted


def _event_types(events: list[dict[str, Any]]) -> list[str]:
    return [event["type"] for event in events]


def _message_item(event: dict[str, Any]) -> dict[str, Any]:
    item = event.get("item")
    assert isinstance(item, dict)
    assert item["type"] == "message"
    return item


def _completed_message(event: dict[str, Any]) -> dict[str, Any]:
    response = event["response"]
    return next(item for item in response["output"] if item["type"] == "message")


def test_text_before_tool_is_released_as_commentary_before_tool_event():
    buffer = _buffer()

    assert buffer.window_id == "thread-1:0"

    emitted = _collect(
        buffer,
        [
            _created(),
            _message_added(),
            _content_added(),
            _text_delta(),
            _tool_added(),
            _text_done(),
            _content_done(),
            _message_done(),
            _tool_args_done(),
            _tool_done(),
            _completed(with_tool=True),
        ],
    )

    types = _event_types(emitted)
    assert types[:5] == [
        ResponsesEventType.RESPONSE_CREATED,
        ResponsesEventType.OUTPUT_ITEM_ADDED,
        ResponsesEventType.CONTENT_PART_ADDED,
        ResponsesEventType.OUTPUT_TEXT_DELTA,
        ResponsesEventType.OUTPUT_ITEM_ADDED,
    ]
    assert _message_item(emitted[1])["phase"] == COMMENTARY_PHASE
    message_done = next(
        event
        for event in emitted
        if event["type"] == ResponsesEventType.OUTPUT_ITEM_DONE
        and event["item"]["type"] == "message"
    )
    assert message_done["item"]["phase"] == COMMENTARY_PHASE
    assert _completed_message(emitted[-1])["phase"] == COMMENTARY_PHASE
    assert emitted[4]["item"]["type"] == "function_call"


def test_text_without_tool_is_released_at_completed_as_final_answer():
    buffer = _buffer()

    emitted_before_completed: list[dict[str, Any]] = []
    for event in [_created(), _message_added(), _content_added(), _text_delta()]:
        emitted_before_completed.extend(buffer.process(event))

    assert _event_types(emitted_before_completed) == [
        ResponsesEventType.RESPONSE_CREATED
    ]

    emitted = emitted_before_completed
    emitted.extend(buffer.process(_text_done()))
    emitted.extend(buffer.process(_content_done()))
    emitted.extend(buffer.process(_message_done()))
    emitted.extend(buffer.process(_completed(with_tool=False)))
    emitted.extend(buffer.flush())

    assert _event_types(emitted) == [
        ResponsesEventType.RESPONSE_CREATED,
        ResponsesEventType.OUTPUT_ITEM_ADDED,
        ResponsesEventType.CONTENT_PART_ADDED,
        ResponsesEventType.OUTPUT_TEXT_DELTA,
        ResponsesEventType.OUTPUT_TEXT_DONE,
        ResponsesEventType.CONTENT_PART_DONE,
        ResponsesEventType.OUTPUT_ITEM_DONE,
        ResponsesEventType.RESPONSE_COMPLETED,
    ]
    assert _message_item(emitted[1])["phase"] == FINAL_ANSWER_PHASE
    assert emitted[6]["item"]["phase"] == FINAL_ANSWER_PHASE
    assert _completed_message(emitted[-1])["phase"] == FINAL_ANSWER_PHASE


def test_tool_without_prior_text_streams_without_empty_message():
    buffer = _buffer()

    emitted = _collect(
        buffer, [_created(), _tool_added(), _tool_args_done(), _tool_done()]
    )

    assert _event_types(emitted) == [
        ResponsesEventType.RESPONSE_CREATED,
        ResponsesEventType.OUTPUT_ITEM_ADDED,
        ResponsesEventType.FUNCTION_CALL_ARGS_DONE,
        ResponsesEventType.OUTPUT_ITEM_DONE,
    ]
    assert all(event.get("item", {}).get("type") != "message" for event in emitted)


def test_completed_tool_output_marks_buffered_message_as_commentary():
    buffer = _buffer()

    emitted = _collect(
        buffer,
        [
            _created(),
            _message_added(),
            _content_added(),
            _text_delta(),
            _completed(with_tool=True),
        ],
    )

    assert _message_item(emitted[1])["phase"] == COMMENTARY_PHASE
    assert _completed_message(emitted[-1])["phase"] == COMMENTARY_PHASE
    assert emitted[-1]["response"]["output"][1]["type"] == "function_call"


@pytest.mark.parametrize(
    ("item_type", "event_type"),
    [
        ("tool_search_call", ResponsesEventType.OUTPUT_ITEM_DONE),
        ("web_search_call", ResponsesEventType.OUTPUT_ITEM_ADDED),
    ],
)
def test_text_before_native_search_is_commentary(item_type: str, event_type: str):
    emitted = _collect(
        _buffer(),
        [
            _created(),
            _message_added(),
            _content_added(),
            _text_delta(),
            _native_search_event(item_type, event_type),
            _text_done(),
            _content_done(),
            _message_done(),
            _completed_with_native_search(item_type),
        ],
    )

    search_index = next(
        index
        for index, event in enumerate(emitted)
        if event.get("item", {}).get("type") == item_type
    )
    assert search_index == 4
    assert _message_item(emitted[1])["phase"] == COMMENTARY_PHASE
    assert _completed_message(emitted[-1])["phase"] == COMMENTARY_PHASE


@pytest.mark.parametrize("item_type", ["tool_search_call", "web_search_call"])
def test_completed_native_search_marks_buffered_message_as_commentary(item_type: str):
    emitted = _collect(
        _buffer(),
        [
            _created(),
            _message_added(),
            _content_added(),
            _text_delta(),
            _completed_with_native_search(item_type),
        ],
    )

    assert _message_item(emitted[1])["phase"] == COMMENTARY_PHASE
    assert _completed_message(emitted[-1])["phase"] == COMMENTARY_PHASE
    assert emitted[-1]["response"]["output"][1]["type"] == item_type


def test_normal_eof_flushes_buffered_text_without_phase():
    buffer = _buffer()

    emitted: list[dict[str, Any]] = []
    for event in [_created(), _message_added(), _content_added(), _text_delta()]:
        emitted.extend(buffer.process(event))
    emitted.extend(buffer.flush())

    assert _event_types(emitted) == [
        ResponsesEventType.RESPONSE_CREATED,
        ResponsesEventType.OUTPUT_ITEM_ADDED,
        ResponsesEventType.CONTENT_PART_ADDED,
        ResponsesEventType.OUTPUT_TEXT_DELTA,
    ]
    assert "phase" not in _message_item(emitted[1])


def test_failed_terminal_flushes_buffered_text_without_final_answer_phase():
    buffer = _buffer()

    emitted = _collect(
        buffer,
        [
            _created(),
            _message_added(),
            _content_added(),
            _text_delta(),
            _failed(),
        ],
    )

    assert _event_types(emitted) == [
        ResponsesEventType.RESPONSE_CREATED,
        ResponsesEventType.OUTPUT_ITEM_ADDED,
        ResponsesEventType.CONTENT_PART_ADDED,
        ResponsesEventType.OUTPUT_TEXT_DELTA,
        ResponsesEventType.RESPONSE_FAILED,
    ]
    assert "phase" not in _message_item(emitted[1])
    assert "phase" not in _completed_message(emitted[-1])


class _FakeStream:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.status_code = 200

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        for chunk in self.chunks:
            yield chunk


class _FakeProcessor:
    def process_chunk(self, chunk: dict[str, Any]) -> list[dict[str, Any]]:
        return chunk["events"]


def test_stream_generator_traces_actual_buffered_downstream_order(tmp_path: Path):
    trace_path = tmp_path / "stream-phase-buffer.jsonl"
    trace = StreamTraceLogger(
        path=trace_path,
        request_id="req-buffer",
        request_log_id="log-buffer",
        model="glm-5.2",
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="Opencode Go",
    )

    async def collect() -> list[str]:
        events: list[str] = []
        async for event in _stream_event_generator(
            source_provider="openai_responses",
            stream=_FakeStream(
                [
                    {
                        "events": [
                            _created(),
                            _message_added(),
                            _content_added(),
                            _text_delta(),
                        ]
                    },
                    {"events": [_tool_added()]},
                ]
            ),
            processor=_FakeProcessor(),
            model="glm-5.2",
            format_sse=lambda event: (
                f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            ),
            event_buffer=_buffer(),
            trace=trace,
        ):
            events.append(event)
        return events

    events = asyncio.run(collect())

    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    downstream = [record for record in records if record["stage"] == "downstream_sse"]
    assert [record["data"] for record in downstream] == events
    assert len(events) == 5
    assert "response.output_text.delta" in events[3]
    assert "function_call" in events[4]
    assert downstream[1]["chunk_index"] == 2
    assert '"phase": "commentary"' in events[1]


def _route() -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="test-provider",
        upstream_model="glm-5.2",
    )


def _route_with_tool_adaptation(tool_adaptation: dict[str, Any]) -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="test-provider",
        upstream_model="glm-5.2",
        tool_adaptation=tool_adaptation,
    )


def _provider_info() -> MagicMock:
    info = MagicMock()
    info.base_url = "https://api.example.test"
    return info


def _chat_text_then_tool_stream() -> _FakeStream:
    return _FakeStream(
        [
            {
                "id": "chatcmpl-stream",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "glm-5.2",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "working"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-stream",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "glm-5.2",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "exec_command",
                                        "arguments": "{}",
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
        ]
    )


def test_handle_streaming_enables_phase_buffer_with_codex_window_id():
    transport = MagicMock()
    transport.send_streaming = AsyncMock(return_value=_chat_text_then_tool_stream())
    body = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "run a command"}],
        "stream": True,
    }

    async def run() -> list[str]:
        response, _ = await handle_streaming(
            _route(),
            _provider_info(),
            body,
            transport=transport,
            codex_window_id="thread-1:0",
        )
        chunks: list[str] = []
        async for chunk in response._generator:
            chunks.append(chunk)
        return chunks

    joined = "\n".join(asyncio.run(run()))

    assert '"phase": "commentary"' in joined
    assert "response.output_text.delta" in joined
    assert "response.output_item.added" in joined


def test_handle_streaming_skips_phase_buffer_without_codex_window_id():
    transport = MagicMock()
    transport.send_streaming = AsyncMock(return_value=_chat_text_then_tool_stream())
    body = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "run a command"}],
        "stream": True,
    }

    async def run() -> list[str]:
        response, _ = await handle_streaming(
            _route(),
            _provider_info(),
            body,
            transport=transport,
            codex_window_id=None,
        )
        chunks: list[str] = []
        async for chunk in response._generator:
            chunks.append(chunk)
        return chunks

    joined = "\n".join(asyncio.run(run()))

    assert '"phase": "commentary"' not in joined
    assert "response.output_text.delta" in joined
    assert "response.output_item.added" in joined


def test_handle_streaming_skips_phase_buffer_when_disabled():
    transport = MagicMock()
    transport.send_streaming = AsyncMock(return_value=_chat_text_then_tool_stream())
    body = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "run a command"}],
        "stream": True,
    }

    async def run() -> list[str]:
        response, _ = await handle_streaming(
            _route_with_tool_adaptation({"enable_phase_detection": False}),
            _provider_info(),
            body,
            transport=transport,
            codex_window_id="thread-1:0",
        )
        chunks: list[str] = []
        async for chunk in response._generator:
            chunks.append(chunk)
        return chunks

    joined = "\n".join(asyncio.run(run()))

    assert '"phase": "commentary"' not in joined
    assert '"phase": "final_answer"' not in joined
    assert "response.output_text.delta" in joined
    assert "response.output_item.added" in joined
