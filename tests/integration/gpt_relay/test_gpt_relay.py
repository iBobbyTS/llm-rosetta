from __future__ import annotations

import json

import pytest

from .capture_proxy import (
    _decode_request,
    _encode_request,
    _join_upstream,
    summarize_request,
)
from .evaluate import evaluate


def test_join_upstream_deduplicates_v1() -> None:
    assert (
        _join_upstream("https://relay.example/v1", "/v1/responses")
        == "https://relay.example/v1/responses"
    )
    assert (
        _join_upstream("https://relay.example", "/v1/responses/compact")
        == "https://relay.example/v1/responses/compact"
    )


@pytest.mark.parametrize("encoding", [None, "zstd"])
def test_request_summary_records_shape_without_prompt(encoding: str | None) -> None:
    decoded = json.dumps(
        {
            "model": "gpt-test",
            "input": [
                {
                    "type": "message",
                    "content": "DO_NOT_RECORD_THIS_PROMPT",
                    "internal_chat_message_metadata_passthrough": {
                        "turn_id": "private"
                    },
                }
            ],
            "stream_options": {"reasoning_summary_delivery": "sequential_cutoff"},
        }
    ).encode()
    encoded = _encode_request(decoded, encoding)
    summary, restored = summarize_request(encoded, encoding)
    assert restored == decoded
    assert summary["model"] == "gpt-test"
    assert summary["input_types"] == ["message"]
    assert summary["has_internal_item_metadata"] is True
    assert summary["reasoning_summary_delivery"] == "sequential_cutoff"
    assert "DO_NOT_RECORD_THIS_PROMPT" not in json.dumps(summary)
    assert "private" not in json.dumps(summary)
    assert _decode_request(encoded, encoding) == decoded


def test_c3_requires_real_completed_zstd_response() -> None:
    result = evaluate(
        scenario="C3",
        representative_provider="relay",
        model="gpt-test",
        process_exit_code=0,
        events=[
            {
                "kind": "request",
                "path": "/v1/responses",
                "model": "gpt-test",
                "content_encoding": "zstd",
            },
            {
                "kind": "response",
                "path": "/v1/responses",
                "status": 200,
                "synthetic": False,
                "forwarded_model": "gpt-test",
                "stream_completed": True,
            },
        ],
    )
    assert result["classification"] == "success"
    assert result["synthetic_codex_backend_auth"] is True


def test_c4_requires_old_rejection_current_fallback_and_real_completion() -> None:
    result = evaluate(
        scenario="C4",
        representative_provider="relay",
        model="gpt-test",
        process_exit_code=0,
        events=[
            {
                "kind": "request",
                "path": "/v1/responses",
                "model": "relay-probe-old",
                "input_types": ["message", "compaction_trigger"],
            },
            {
                "kind": "response",
                "path": "/v1/responses/compact",
                "status": 400,
                "synthetic": True,
            },
            {
                "kind": "request",
                "path": "/v1/responses",
                "model": "gpt-test",
                "input_types": ["message", "compaction_trigger"],
            },
            {
                "kind": "response",
                "path": "/v1/responses/compact",
                "status": 200,
                "synthetic": False,
                "forwarded_model": "gpt-test",
            },
            {
                "kind": "response",
                "path": "/v1/responses",
                "status": 200,
                "synthetic": False,
                "forwarded_model": "gpt-test",
                "stream_completed": True,
            },
        ],
    )
    assert result["classification"] == "success"
    assert result["compact_requested_models"] == ["relay-probe-old", "gpt-test"]


def test_c4_accepts_retried_follow_up_as_success_with_deviation() -> None:
    result = evaluate(
        scenario="C4",
        representative_provider="relay",
        model="gpt-test",
        process_exit_code=0,
        events=[
            {
                "kind": "request",
                "model": "relay-probe-old",
                "input_types": ["compaction_trigger"],
            },
            {"kind": "response", "status": 400, "synthetic": True},
            {
                "kind": "request",
                "model": "gpt-test",
                "input_types": ["compaction_trigger"],
            },
            {
                "kind": "response",
                "status": 200,
                "synthetic": False,
                "forwarded_model": "gpt-test",
                "stream_completed": True,
            },
            {
                "kind": "response",
                "status": 200,
                "synthetic": False,
                "forwarded_model": "gpt-test",
                "event_names": ["response.failed"],
            },
            {
                "kind": "response",
                "status": 200,
                "synthetic": False,
                "forwarded_model": "gpt-test",
                "stream_completed": True,
            },
        ],
    )
    assert result["classification"] == "success_with_deviation"


def test_c5_rejects_openai_only_request_shapes() -> None:
    result = evaluate(
        scenario="C5",
        representative_provider="relay",
        model="gpt-test",
        process_exit_code=0,
        events=[
            {
                "kind": "request",
                "path": "/v1/responses",
                "model": "gpt-test",
                "content_encoding": "zstd",
            },
            {
                "kind": "response",
                "path": "/v1/responses",
                "status": 200,
                "synthetic": False,
                "forwarded_model": "gpt-test",
                "stream_completed": True,
            },
        ],
    )
    assert result["classification"] == "failure"
