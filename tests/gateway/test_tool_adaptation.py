"""Tests for Codex tool localization at the gateway boundary."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from llm_rosetta.gateway.proxy import (
    ProviderMetadataStore,
    handle_non_streaming,
    handle_streaming,
)
from llm_rosetta.gateway.tool_adaptation import (
    CodexToolLocalizationStore,
    LOCALIZED_CODE_TOOL_NAMES,
    LocalizedToolCallStreamTransformer,
    localized_mapping_from_tool_calls,
    generated_patch_for_edit,
    localize_code_editing_chat_request,
    translate_localized_tool_call_part,
)
from llm_rosetta.observability.persistence import PersistenceManager
from llm_rosetta.gateway.transport._base import UpstreamResponse, UpstreamStream
from llm_rosetta.routing import ResolvedRoute


def _route() -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="test-provider",
        upstream_model="glm-5.2",
        tool_adaptation={"localize_code_editing_tools": True},
    )


def _provider_info() -> MagicMock:
    info = MagicMock()
    info.base_url = "https://api.example.test"
    return info


def _tool_names(tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        function = tool.get("function")
        if isinstance(function, dict):
            names.add(function.get("name", ""))
        elif tool.get("name"):
            names.add(tool["name"])
        elif tool.get("type"):
            names.add(tool["type"])
    return names


def test_localize_code_editing_chat_request_replaces_native_tools():
    body = {
        "model": "glm-5.2",
        "messages": [{"role": "user", "content": "edit file"}],
        "tool_choice": {"type": "function", "function": {"name": "apply_patch"}},
        "tools": [
            {"type": "function", "function": {"name": "exec_command"}},
            {"type": "function", "function": {"name": "write_stdin"}},
            {"type": "function", "function": {"name": "apply_patch"}},
            {"type": "function", "function": {"name": "view_image"}},
        ],
    }

    adapted = localize_code_editing_chat_request(body)

    names = _tool_names(adapted["tools"])
    assert {"exec_command", "write_stdin", "apply_patch"}.isdisjoint(names)
    assert LOCALIZED_CODE_TOOL_NAMES.issubset(names)
    assert "view_image" in names
    assert adapted["tool_choice"] == "auto"
    assert body["tools"][0]["function"]["name"] == "exec_command"


def test_translate_localized_bash_to_exec_command():
    translated = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_bash",
            "tool_name": "Bash",
            "tool_input": {"command": "printf ok", "timeout": 500},
        }
    )

    assert translated is not None
    assert translated.part["tool_name"] == "exec_command"
    assert translated.part["tool_input"]["cmd"] == "printf ok"
    assert translated.part["tool_input"]["yield_time_ms"] == 500
    assert translated.mapping.localized_name == "Bash"


def test_translate_localized_edit_to_custom_apply_patch():
    translated = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_edit",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "src/app.py",
                "old_string": "print('old')",
                "new_string": "print('new')",
            },
        }
    )

    assert translated is not None
    assert translated.part["tool_name"] == "apply_patch"
    assert translated.part["tool_type"] == "custom"
    patch = translated.part["tool_input"]["input"]
    assert "*** Update File: src/app.py" in patch
    assert "-print('old')" in patch
    assert "+print('new')" in patch


def test_invalid_localized_call_becomes_meaningful_exec_error():
    translated = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_bad",
            "tool_name": "Bash",
            "tool_input": {"description": "missing command"},
        }
    )

    assert translated is not None
    assert translated.part["tool_name"] == "exec_command"
    completed = subprocess.run(
        translated.part["tool_input"]["cmd"],
        shell=True,
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert completed.returncode == 1
    assert "Tool adaptation error" in completed.stderr
    assert "Bash requires string field 'command'" in completed.stderr


def test_stream_transformer_buffers_localized_call_until_finish():
    transformer = LocalizedToolCallStreamTransformer()

    assert (
        transformer.transform(
            {
                "type": "tool_call_start",
                "tool_call_id": "call_bash",
                "tool_name": "Bash",
                "tool_call_index": 0,
            }
        )
        == []
    )
    assert (
        transformer.transform(
            {
                "type": "tool_call_delta",
                "tool_call_id": "call_bash",
                "arguments_delta": '{"command": "printf ok"}',
            }
        )
        == []
    )

    events = transformer.transform(
        {"type": "finish", "finish_reason": {"reason": "tool_calls"}}
    )

    assert [event["type"] for event in events] == [
        "tool_call_start",
        "tool_call_delta",
        "finish",
    ]
    assert events[0]["tool_name"] == "exec_command"
    assert json.loads(events[1]["arguments_delta"]) == {"cmd": "printf ok"}


def test_gateway_non_streaming_localizes_request_and_returns_native_tool_call():
    captured_body: dict[str, Any] = {}
    upstream_body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 123,
        "model": "glm-5.2",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_edit",
                            "type": "function",
                            "function": {
                                "name": "Edit",
                                "arguments": json.dumps(
                                    {
                                        "file_path": "example.txt",
                                        "old_string": "old",
                                        "new_string": "new",
                                    }
                                ),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    async def send_request(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        captured_body.update(body)
        return UpstreamResponse(
            status_code=200,
            body=upstream_body,
            raw_content=json.dumps(upstream_body).encode(),
        )

    transport = MagicMock()
    transport.send_request = AsyncMock(side_effect=send_request)
    body = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "edit example.txt"}],
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "type": "custom",
                "name": "apply_patch",
                "description": "Apply patch",
            },
        ],
    }
    tool_store = CodexToolLocalizationStore()

    async def run():
        return await handle_non_streaming(
            _route(),
            _provider_info(),
            body,
            transport=transport,
            metadata_store=ProviderMetadataStore(),
            codex_tool_store=tool_store,
        )

    response, profile = asyncio.run(run())

    assert response.status_code == 200
    assert "request_conversion_ms" in profile
    assert LOCALIZED_CODE_TOOL_NAMES.issubset(_tool_names(captured_body["tools"]))
    assert {"exec_command", "apply_patch"}.isdisjoint(
        _tool_names(captured_body["tools"])
    )

    source_body = json.loads(response.body)
    output = source_body["output"]
    assert output[0]["type"] == "custom_tool_call"
    assert output[0]["name"] == "apply_patch"
    assert "*** Update File: example.txt" in output[0]["input"]

    next_chat_request = localize_code_editing_chat_request(
        {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_edit",
                            "type": "function",
                            "function": {
                                "name": "apply_patch",
                                "arguments": json.dumps({"input": output[0]["input"]}),
                            },
                        }
                    ],
                }
            ]
        },
        store=tool_store,
    )
    restored = next_chat_request["messages"][0]["tool_calls"][0]["function"]
    assert restored["name"] == "Edit"
    assert json.loads(restored["arguments"])["old_string"] == "old"


def test_persisted_mapping_restores_history_without_memory_store():
    translated = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_edit",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "example.txt",
                "old_string": "old",
                "new_string": "new",
            },
        }
    )
    assert translated is not None
    mapping = localized_mapping_from_tool_calls(
        translated.mapping.original_tool_call(),
        translated.mapping.codex_tool_call(),
    )
    assert mapping is not None
    used_call_ids: set[str] = set()

    adapted = localize_code_editing_chat_request(
        {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [translated.mapping.codex_tool_call()],
                }
            ]
        },
        mappings=[mapping],
        used_call_ids=used_call_ids,
    )

    function = adapted["messages"][0]["tool_calls"][0]["function"]
    assert function["name"] == "Edit"
    assert json.loads(function["arguments"])["old_string"] == "old"
    assert used_call_ids == {"call_edit"}


def test_gateway_non_streaming_persists_and_reuses_tool_mapping(tmp_path):
    captured_bodies: list[dict[str, Any]] = []
    upstream_body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 123,
        "model": "glm-5.2",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_edit",
                            "type": "function",
                            "function": {
                                "name": "Edit",
                                "arguments": json.dumps(
                                    {
                                        "file_path": "example.txt",
                                        "old_string": "old",
                                        "new_string": "new",
                                    }
                                ),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    async def send_request(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        captured_bodies.append(body)
        return UpstreamResponse(
            status_code=200,
            body=upstream_body,
            raw_content=json.dumps(upstream_body).encode(),
        )

    transport = MagicMock()
    transport.send_request = AsyncMock(side_effect=send_request)
    persistence = PersistenceManager(str(tmp_path))
    body = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "edit example.txt"}],
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "parameters": {"type": "object", "properties": {}},
            },
            {"type": "custom", "name": "apply_patch", "description": "Apply patch"},
        ],
    }

    async def first_run():
        return await handle_non_streaming(
            _route(),
            _provider_info(),
            body,
            transport=transport,
            metadata_store=ProviderMetadataStore(),
            codex_tool_store=CodexToolLocalizationStore(),
            persistence=persistence,
            tool_cache_session_id="window-1",
        )

    first_response, _ = asyncio.run(first_run())
    first_output = json.loads(first_response.body)["output"][0]
    assert persistence.count_tool_call_mappings() == 1

    second_body = {
        "model": "glm-5.2",
        "input": [
            {
                "type": "custom_tool_call",
                "call_id": "call_edit",
                "name": "apply_patch",
                "input": first_output["input"],
            },
            {"role": "user", "content": "continue"},
        ],
        "tools": body["tools"],
    }

    async def second_run():
        return await handle_non_streaming(
            _route(),
            _provider_info(),
            second_body,
            transport=transport,
            metadata_store=ProviderMetadataStore(),
            codex_tool_store=CodexToolLocalizationStore(),
            persistence=persistence,
            tool_cache_session_id="window-1",
        )

    asyncio.run(second_run())
    restored_calls = captured_bodies[-1]["messages"][0]["tool_calls"]
    assert restored_calls[0]["function"]["name"] == "Edit"
    assert persistence.count_tool_call_mappings() == 1
    persistence.close()


def test_gateway_deletes_unused_persistent_mappings_after_request(tmp_path):
    captured_body: dict[str, Any] = {}
    persistence = PersistenceManager(str(tmp_path))
    persistence.upsert_tool_call_mapping(
        session_id="window-1",
        tool_call_id="unused",
        original_tool_call={
            "id": "unused",
            "type": "function",
            "function": {"name": "Bash", "arguments": '{"command":"pwd"}'},
        },
        codex_tool_call={
            "id": "unused",
            "type": "function",
            "function": {"name": "exec_command", "arguments": '{"cmd":"pwd"}'},
        },
        expire_at="2030-01-01T00:00:00+00:00",
        timestamp="2026-01-01T00:00:00+00:00",
    )

    async def send_request(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        captured_body.update(body)
        return UpstreamResponse(
            status_code=200,
            body={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 123,
                "model": "glm-5.2",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
            },
            raw_content=b"{}",
        )

    transport = MagicMock()
    transport.send_request = AsyncMock(side_effect=send_request)
    body = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    }

    async def run():
        return await handle_non_streaming(
            _route(),
            _provider_info(),
            body,
            transport=transport,
            metadata_store=ProviderMetadataStore(),
            codex_tool_store=CodexToolLocalizationStore(),
            persistence=persistence,
            tool_cache_session_id="window-1",
        )

    asyncio.run(run())
    assert "messages" in captured_body
    assert persistence.count_tool_call_mappings() == 0
    persistence.close()


class _ChatStream(UpstreamStream):
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.status_code = 200
        self._chunks = chunks
        self.closed = False

    async def read_error(self) -> str:
        return ""

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        for chunk in self._chunks:
            yield chunk

    def aiter_raw_bytes(self):
        return None

    async def close(self) -> None:
        self.closed = True


def test_gateway_streaming_localizes_request_and_returns_native_tool_events():
    captured_body: dict[str, Any] = {}
    stream = _ChatStream(
        [
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
                                    "id": "call_bash",
                                    "type": "function",
                                    "function": {
                                        "name": "Bash",
                                        "arguments": '{"command":',
                                    },
                                }
                            ]
                        },
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
                                    "function": {"arguments": '"printf ok"}'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-stream",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "glm-5.2",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
            {
                "id": "chatcmpl-stream",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "glm-5.2",
                "choices": [],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        ]
    )

    async def send_streaming(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        captured_body.update(body)
        return stream

    transport = MagicMock()
    transport.send_streaming = AsyncMock(side_effect=send_streaming)
    body = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "run a command"}],
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        "stream": True,
    }

    async def run() -> list[str]:
        response, profile = await handle_streaming(
            _route(),
            _provider_info(),
            body,
            transport=transport,
            metadata_store=ProviderMetadataStore(),
            codex_tool_store=CodexToolLocalizationStore(),
        )
        assert response.status_code == 200
        assert "request_conversion_ms" in profile
        chunks: list[str] = []
        async for chunk in response._generator:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(run())

    assert LOCALIZED_CODE_TOOL_NAMES.issubset(_tool_names(captured_body["tools"]))
    assert "exec_command" not in _tool_names(captured_body["tools"])
    joined = "\n".join(chunks)
    assert "response.output_item.added" in joined
    assert '"name": "exec_command"' in joined
    assert "response.function_call_arguments.delta" in joined
    assert '\\"cmd\\": \\"printf ok\\"' in joined
    assert '"name": "Bash"' not in joined


def test_gateway_streaming_persists_tool_mapping(tmp_path):
    stream = _ChatStream(
        [
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
                                    "id": "call_bash",
                                    "type": "function",
                                    "function": {
                                        "name": "Bash",
                                        "arguments": '{"command": "printf ok"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-stream",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "glm-5.2",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
    )

    async def send_streaming(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        return stream

    transport = MagicMock()
    transport.send_streaming = AsyncMock(side_effect=send_streaming)
    persistence = PersistenceManager(str(tmp_path))
    body = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "run a command"}],
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        "stream": True,
    }

    async def run() -> list[str]:
        response, _ = await handle_streaming(
            _route(),
            _provider_info(),
            body,
            transport=transport,
            metadata_store=ProviderMetadataStore(),
            codex_tool_store=CodexToolLocalizationStore(),
            persistence=persistence,
            tool_cache_session_id="window-1",
        )
        chunks: list[str] = []
        async for chunk in response._generator:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(run())

    assert '"name": "exec_command"' in "\n".join(chunks)
    rows = persistence.query_tool_call_mappings(
        session_id="window-1",
        now="2026-01-01T00:00:00+00:00",
    )
    assert rows[0]["original_tool_call"]["function"]["name"] == "Bash"
    assert rows[0]["codex_tool_call"]["function"]["name"] == "exec_command"
    persistence.close()


def test_safe_executor_runs_generated_bash_write_and_edit(tmp_path):
    bash_call = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_bash",
            "tool_name": "Bash",
            "tool_input": {"command": "printf 'ok' > bash.txt"},
        }
    )
    assert bash_call is not None
    result = _execute_native_call(tmp_path, bash_call.part)
    assert result["ok"], result
    assert (tmp_path / "bash.txt").read_text() == "ok"

    write_call = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_write",
            "tool_name": "Write",
            "tool_input": {"file_path": "notes.txt", "content": "alpha\nbeta\n"},
        }
    )
    assert write_call is not None
    result = _execute_native_call(tmp_path, write_call.part)
    assert result["ok"], result
    assert (tmp_path / "notes.txt").read_text() == "alpha\nbeta\n"

    edit_call = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_edit",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "notes.txt",
                "old_string": "beta",
                "new_string": "gamma",
            },
        }
    )
    assert edit_call is not None
    result = _execute_native_call(tmp_path, edit_call.part)
    assert result["ok"], result
    assert (tmp_path / "notes.txt").read_text() == "alpha\ngamma\n"


def test_safe_executor_reports_meaningful_edit_failure(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n")
    patch = generated_patch_for_edit("notes.txt", "missing", "replacement")

    result = _apply_test_patch(tmp_path, patch)

    assert not result["ok"]
    assert "old_string was not found" in result["stderr"]
    assert "alpha" in result["stderr"]


def _execute_native_call(tmp_path: Path, part: dict[str, Any]) -> dict[str, Any]:
    if part["tool_name"] == "exec_command":
        completed = subprocess.run(
            part["tool_input"]["cmd"],
            cwd=tmp_path,
            shell=True,
            text=True,
            capture_output=True,
            timeout=5,
        )
        return {
            "ok": completed.returncode == 0,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    if part["tool_name"] == "apply_patch":
        return _apply_test_patch(tmp_path, part["tool_input"]["input"])
    raise AssertionError(f"Unsupported native test tool: {part['tool_name']}")


def _apply_test_patch(tmp_path: Path, patch: str) -> dict[str, Any]:
    lines = patch.splitlines()
    if len(lines) < 5 or lines[0] != "*** Begin Patch":
        return {"ok": False, "stdout": "", "stderr": "Invalid patch header"}
    update_lines = [line for line in lines if line.startswith("*** Update File: ")]
    if len(update_lines) != 1:
        return {
            "ok": False,
            "stdout": "",
            "stderr": "Test executor supports one update hunk",
        }
    rel_path = update_lines[0].split(": ", 1)[1]
    target = (tmp_path / rel_path).resolve()
    if not str(target).startswith(str(tmp_path.resolve())):
        return {"ok": False, "stdout": "", "stderr": "Path escapes test root"}

    old_lines: list[str] = []
    new_lines: list[str] = []
    for line in lines:
        if line.startswith("-"):
            old_lines.append(line[1:])
        elif line.startswith("+"):
            new_lines.append(line[1:])
    old = "\n".join(old_lines)
    new = "\n".join(new_lines)
    text = target.read_text()
    count = text.count(old)
    if count == 0:
        context = text[:200]
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"Edit failed: old_string was not found in {rel_path}. Context:\n{context}",
        }
    if count > 1:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"Edit failed: old_string matched {count} times in {rel_path}.",
        }
    target.write_text(text.replace(old, new, 1))
    return {"ok": True, "stdout": f"Patched {rel_path}\n", "stderr": ""}
