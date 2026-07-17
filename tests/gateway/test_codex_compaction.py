"""Unit tests for Rosetta's private Codex Remote Compaction V2 coordinator."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codex_rosetta._vendor.httpserver import JSONResponse, Response, StreamingResponse
from codex_rosetta.gateway import proxy
from codex_rosetta.gateway.codex_compaction import (
    COMPACT_PROMPT,
    COMPACT_PROMPT_SHA256,
    SUMMARY_PREFIX,
    InvalidCodexCompactionRequest,
    InvalidCompactionSummary,
    build_compaction_response,
    create_compaction_mapping,
    extract_assistant_summary,
    prepare_codex_compaction,
)
from codex_rosetta.gateway.state_scope import GatewayStateScope
from codex_rosetta.observability.persistence import PersistenceManager
from codex_rosetta.routing import ResolvedRoute


def _route(*, passthrough: bool = False) -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_responses" if passthrough else "openai_chat",
        provider_name="test",
    )


def _request(reason: str = "context_limit") -> dict:
    return {
        "model": "deepseek-v4-flash",
        "input": [
            {"type": "message", "role": "user", "content": "history"},
            {"type": "compaction_trigger"},
        ],
        "tools": [{"type": "function", "name": "unwanted"}],
        "tool_choice": "required",
        "parallel_tool_calls": True,
        "additional_tools": [{"type": "computer"}],
        "client_metadata": {
            "x-codex-turn-metadata": json.dumps({"compaction": {"reason": reason}}),
            "keep": "this",
        },
    }


@pytest.mark.parametrize(
    ("passthrough", "reason", "expected"),
    [
        (True, "context_limit", "native"),
        (True, "user_requested", "native"),
        (True, "comp_hash_changed", "rosetta"),
        (True, "model_downshift", "rosetta"),
        (False, "context_limit", "rosetta"),
        (False, "user_requested", "rosetta"),
        (False, "comp_hash_changed", "rosetta"),
        (False, "model_downshift", "rosetta"),
    ],
)
def test_policy_uses_only_route_configuration_and_metadata_reason(
    passthrough: bool, reason: str, expected: str | None
) -> None:
    prepared = prepare_codex_compaction(
        _request(reason),
        route=_route(passthrough=passthrough),
        persistence=None,
        principal_id="client-a",
    )
    assert prepared.mode == expected
    if expected == "native":
        assert prepared.body == _request(reason)
        assert prepared.summary_request is None
    else:
        assert prepared.summary_request is not None


def test_malformed_metadata_or_header_never_promotes_native_passthrough() -> None:
    body = _request()
    body["client_metadata"]["x-codex-turn-metadata"] = "not json"
    body["x-codex-turn-metadata"] = json.dumps(
        {"compaction": {"reason": "context_limit"}}
    )
    prepared = prepare_codex_compaction(
        body, route=_route(passthrough=True), persistence=None, principal_id="client-a"
    )
    assert prepared.reason == "unknown"
    assert prepared.mode == "rosetta"


@pytest.mark.parametrize(
    "input_items",
    [
        [{"type": "compaction_trigger"}, {"type": "message"}],
        [{"type": "compaction_trigger"}, {"type": "compaction_trigger"}],
    ],
)
def test_invalid_trigger_sequence_is_rejected(input_items: list[dict]) -> None:
    body = _request()
    body["input"] = input_items
    with pytest.raises(InvalidCodexCompactionRequest):
        prepare_codex_compaction(
            body, route=_route(), persistence=None, principal_id="client-a"
        )


def test_rosetta_summary_request_strips_tools_and_preserves_other_fields() -> None:
    body = _request("comp_hash_changed")
    body["input"].insert(
        0,
        {
            "type": "additional_tools",
            "tools": [{"type": "function", "name": "lite-unwanted"}],
        },
    )
    body["custom_field"] = {"preserved": True}
    prepared = prepare_codex_compaction(
        body, route=_route(), persistence=None, principal_id="client-a"
    )
    assert prepared.summary_request is not None
    request = prepared.summary_request
    assert request["custom_field"] == {"preserved": True}
    assert request["stream"] is False
    assert all(
        key not in request
        for key in ("tools", "tool_choice", "parallel_tool_calls", "additional_tools")
    )
    assert all(item.get("type") != "additional_tools" for item in request["input"])
    assert request["client_metadata"] == {"keep": "this"}
    assert request["input"][-1]["content"][0]["text"] == COMPACT_PROMPT


def test_mapping_replays_only_for_its_principal_and_stores_prefixed_plaintext(
    tmp_path,
) -> None:
    persistence = PersistenceManager(str(tmp_path))
    created = create_compaction_mapping(
        persistence,
        principal_id="client-a",
        source_model="deepseek-v4-flash",
        reason="comp_hash_changed",
        summary="Orchid remains unchanged.",
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    row = persistence.get_codex_compaction_mapping(
        principal_id="client-a",
        token_hash=hashlib.sha256(created.token.encode()).hexdigest(),
        now="2026-07-02T00:00:00+00:00",
    )
    assert row is not None
    assert row["replacement_text"] == f"{SUMMARY_PREFIX}\n\nOrchid remains unchanged."
    assert row["prompt_sha256"] == COMPACT_PROMPT_SHA256

    request = {
        "model": "x",
        "input": [{"type": "compaction", "encrypted_content": created.token}],
    }
    restored = prepare_codex_compaction(
        request,
        route=_route(passthrough=True),
        persistence=persistence,
        principal_id="client-a",
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    assert restored.rehydrated_count == 1
    assert restored.body["input"][0]["content"][0]["text"] == row["replacement_text"]
    cross_principal = prepare_codex_compaction(
        request,
        route=_route(passthrough=True),
        persistence=persistence,
        principal_id="client-b",
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    assert cross_principal.body["input"] == []
    assert cross_principal.dropped_rosetta_count == 1
    persistence.close()


def test_non_rosetta_compaction_is_preserved_only_for_native_responses_route() -> None:
    request = {
        "model": "x",
        "input": [{"type": "compaction", "encrypted_content": "upstream"}],
    }
    assert (
        prepare_codex_compaction(
            request, route=_route(passthrough=True), persistence=None, principal_id="a"
        ).body["input"]
        == request["input"]
    )
    assert (
        prepare_codex_compaction(
            request, route=_route(), persistence=None, principal_id="a"
        ).body["input"]
        == []
    )


def test_summary_extraction_rejects_tools_and_empty_output() -> None:
    with pytest.raises(InvalidCompactionSummary):
        extract_assistant_summary({"output": [{"type": "function_call"}]})
    with pytest.raises(InvalidCompactionSummary):
        extract_assistant_summary(
            {"output": [{"type": "message", "role": "assistant", "content": []}]}
        )
    assert (
        extract_assistant_summary(
            {
                "output": [
                    {"type": "reasoning"},
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "summary"}],
                    },
                ]
            }
        )
        == "summary"
    )


def test_canonical_non_stream_and_sse_lifecycle() -> None:
    token = "rskc_v1_test"
    response = build_compaction_response(model="model", token=token, stream=False)
    assert isinstance(response, Response)
    payload = json.loads(response.body)
    assert payload["output"] and payload["output"][0]["type"] == "compaction"
    assert payload["output"][0]["encrypted_content"] == token

    streamed = build_compaction_response(model="model", token=token, stream=True)
    assert isinstance(streamed, StreamingResponse)

    async def collect() -> list[str]:
        return [
            chunk if isinstance(chunk, str) else chunk.decode("utf-8")
            async for chunk in streamed._generator
        ]

    chunks = asyncio.run(collect())
    assert [chunk.split("\n", 1)[0] for chunk in chunks] == [
        "event: response.created",
        "event: response.output_item.added",
        "event: response.output_item.done",
        "event: response.completed",
    ]


def test_bundled_prompt_hash_is_stable() -> None:
    assert COMPACT_PROMPT_SHA256 == hashlib.sha256(COMPACT_PROMPT.encode()).hexdigest()


def test_internal_summary_retains_persistence_but_disables_body_logging(
    tmp_path, monkeypatch
) -> None:
    persistence = PersistenceManager(str(tmp_path))
    prepared = prepare_codex_compaction(
        _request("comp_hash_changed"),
        route=_route(),
        persistence=persistence,
        principal_id="client-a",
    )
    captured: dict = {}

    async def fake_handle_non_streaming(*args, **kwargs):
        captured.update(kwargs)
        return (
            JSONResponse(
                {
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "summary"}],
                        }
                    ]
                }
            ),
            {},
        )

    monkeypatch.setattr(proxy, "handle_non_streaming", fake_handle_non_streaming)
    response, _ = asyncio.run(
        proxy._run_rosetta_compaction(
            route=_route(),
            provider_info=MagicMock(),
            preparation=prepared,
            transport=MagicMock(),
            metadata_store=None,
            codex_tool_store=None,
            extra_headers=None,
            persistence=persistence,
            state_scope=GatewayStateScope.for_request(
                principal_id="client-a",
                provider_name="test",
                model="deepseek-v4-flash",
                window_id="thread-a:0",
            ),
            codex_window_id="thread-a:0",
            image_fetch_workers=None,
            stream=False,
        )
    )

    assert response.status_code == 200
    assert captured["persistence"] is persistence
    assert captured["body_log_state"] is None
    assert captured["upstream_error_log_state"] is None
    assert captured["skip_codex_compaction"] is True
    assert captured["disable_error_dump"] is True
    assert persistence.count_codex_compaction_mappings() == 1
    persistence.close()


def test_live_quality_matrix_uses_identical_input_and_locked_provider_routes() -> None:
    live_root = Path(__file__).parents[2] / "tests" / "live_agent"
    quality = live_root / "context_compaction_summary_quality"

    assert (quality / "01" / "TASK.md").read_bytes() == (
        quality / "02" / "TASK.md"
    ).read_bytes()
    assert (quality / "01" / "scenario.py").read_bytes() == (
        quality / "02" / "scenario.py"
    ).read_bytes()
    assert (quality / "01" / "QUERY.md").read_bytes() == (
        quality / "02" / "QUERY.md"
    ).read_bytes()

    gpt = json.loads((quality / "01" / "expected.json").read_text())
    deepseek = json.loads((quality / "02" / "expected.json").read_text())
    assert gpt["gateway_provider"] == "Pixel (K12)"
    assert deepseek["model"] == "deepseek-v4-flash"
    assert gpt["model_auto_compact_token_limit"] == 15000
    assert deepseek["model_auto_compact_token_limit"] == 15000
    assert gpt["expected_compaction_count"] == 1
    assert deepseek["expected_compaction_count"] == 1
    assert gpt["phase1_marker"] == "PHASE1:QUALITY_CONTEXT_READY"
    assert deepseek["phase1_marker"] == gpt["phase1_marker"]
    assert gpt["resume_prompt_file"] == "QUERY.md"
    assert deepseek["resume_prompt_file"] == "QUERY.md"
    assert gpt["resume_auto_compact_token_limit"] == 1_000_000
    assert deepseek["resume_auto_compact_token_limit"] == 1_000_000
    assert gpt["expected_resume_compaction_count"] == 0
    assert deepseek["expected_resume_compaction_count"] == 0

    facts = json.loads((quality / "expected_facts.json").read_text())
    assert set(facts) == {
        "project",
        "completed_stage",
        "immutable_file",
        "timezone",
        "active_endpoint",
        "superseded_endpoint",
        "predeploy_gate",
        "reference_code",
        "rollout_strategy",
        "strategy_reason",
        "deployment_owner",
    }
    model_prompts = (quality / "01" / "TASK.md").read_text() + (
        quality / "01" / "QUERY.md"
    ).read_text()
    for value in facts.values():
        if isinstance(value, str):
            assert value not in model_prompts


def test_protocol_context_limit_cells_use_identical_non_quality_fixture() -> None:
    suite = Path(__file__).parents[2] / "tests" / "live_agent" / "context_compaction"
    assert (suite / "01" / "TASK.md").read_bytes() == (
        suite / "02" / "TASK.md"
    ).read_bytes()
    assert (suite / "01" / "scenario.py").read_bytes() == (
        suite / "02" / "scenario.py"
    ).read_bytes()
