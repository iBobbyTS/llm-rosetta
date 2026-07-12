"""Evaluate captured GPT relay A/B requests without reading prompt bodies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def evaluate(
    *,
    scenario: str,
    representative_provider: str,
    model: str,
    events: list[dict[str, Any]],
    process_exit_code: int,
) -> dict[str, Any]:
    requests = [event for event in events if event.get("kind") == "request"]
    responses = [event for event in events if event.get("kind") == "response"]
    real_responses = [event for event in responses if not event.get("synthetic")]
    stream_completed = any(event.get("stream_completed") for event in real_responses)
    stream_failed = any(
        "response.failed" in event.get("event_names", []) for event in real_responses
    )
    actual_models = sorted(
        {
            model_name
            for event in real_responses
            if isinstance((model_name := event.get("forwarded_model")), str)
        }
    )
    zstd_sent = any(
        "zstd" in str(event.get("content_encoding", "")).lower() for event in requests
    )
    sequential_cutoff_sent = any(
        event.get("reasoning_summary_delivery") == "sequential_cutoff"
        for event in requests
    )
    metadata_sent = any(event.get("has_internal_item_metadata") for event in requests)
    transport_adapted = any(event.get("transport_adapted") for event in requests)
    compact_requests = [
        event
        for event in requests
        if "compaction_trigger" in event.get("input_types", [])
    ]
    synthetic_rejection = any(
        event.get("synthetic") and event.get("status") == 400 for event in responses
    )
    transport_success = (
        process_exit_code == 0
        and bool(real_responses)
        and all(200 <= int(event.get("status", 0)) < 300 for event in real_responses)
    )
    success = transport_success and not stream_failed
    if scenario in {"C0", "C1", "C2", "C3", "C5"}:
        success = success and stream_completed
    if scenario == "C3":
        success = success and zstd_sent
    if scenario == "C4":
        requested_models = [event.get("model") for event in compact_requests]
        success = (
            transport_success
            and synthetic_rejection
            and requested_models[:2] == ["relay-probe-old", model]
            and bool(real_responses[-1].get("stream_completed"))
        )
    if scenario == "C5":
        success = success and not zstd_sent and not sequential_cutoff_sent
    classification = (
        "success_with_deviation" if success and stream_failed else "success"
    )
    if not success:
        classification = "failure"
    return {
        "suite": "gpt_relay_compatibility",
        "scenario": scenario,
        "representative_provider": representative_provider,
        "model": model,
        "openai_identity": scenario in {"C2", "C3", "C4"},
        "synthetic_codex_backend_auth": scenario in {"C3", "C4", "C5"},
        "request_count": len(requests),
        "response_count": len(responses),
        "actual_upstream_models": actual_models,
        "sequential_cutoff_sent": sequential_cutoff_sent,
        "internal_item_metadata_sent": metadata_sent,
        "zstd_sent": zstd_sent,
        "transport_adapted": transport_adapted,
        "model_switch_fallback_attempted": synthetic_rejection,
        "compact_requested_models": [event.get("model") for event in compact_requests],
        "stream_completed": stream_completed,
        "stream_failed": stream_failed,
        "process_exit_code": process_exit_code,
        "classification": classification,
        "error": None if success else "scenario acceptance criteria were not met",
    }
