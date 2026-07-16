"""Codex Remote Compaction V2 request coordination.

This module deliberately owns the Rosetta-only state machine.  The proxy
only decides when to run the internal summary request, keeping regular
conversion and direct Responses passthrough free of compaction policy.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import resources
from typing import Any

from codex_rosetta._vendor.httpserver import JSONResponse, Response, StreamingResponse
from codex_rosetta.routing import ResolvedRoute, is_responses_passthrough

from .transport.sse_format import SSE_FORMATTERS

COMPACTION_TRIGGER_TYPE = "compaction_trigger"
COMPACTION_ITEM_TYPE = "compaction"
ROSETTA_TOKEN_PREFIX = "rskc_v1_"
COMPACTION_TTL = timedelta(days=7)
NATIVE_COMPACTION_REASONS = frozenset({"context_limit", "user_requested"})


class InvalidCodexCompactionRequest(ValueError):
    """The request contains an invalid V2 compaction trigger."""


class InvalidCompactionSummary(ValueError):
    """The internal summary response is not a usable assistant summary."""


@dataclass(frozen=True)
class CompactionPreparation:
    """A validated request and its selected compaction execution mode."""

    body: dict[str, Any]
    mode: str | None
    reason: str
    summary_request: dict[str, Any] | None
    rehydrated_count: int
    dropped_rosetta_count: int
    dropped_native_count: int


@dataclass(frozen=True)
class CreatedCompactionMapping:
    """The opaque token returned to Codex after a Rosetta summary."""

    token: str
    prompt_sha256: str


def _resource_text(name: str) -> str:
    return (
        resources.files("codex_rosetta.gateway")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


COMPACT_PROMPT = _resource_text("codex_compact_prompt.md")
SUMMARY_PREFIX = _resource_text("codex_compact_summary_prefix.md").rstrip("\n")
COMPACT_PROMPT_SHA256 = hashlib.sha256(COMPACT_PROMPT.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_rosetta_token(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(ROSETTA_TOKEN_PREFIX)


def _compaction_reason(body: dict[str, Any]) -> str:
    """Read the only authoritative V2 reason from body client metadata."""
    metadata = body.get("client_metadata")
    if not isinstance(metadata, dict):
        return "unknown"
    raw = metadata.get("x-codex-turn-metadata")
    if not isinstance(raw, str):
        return "unknown"
    try:
        decoded = json.loads(raw)
    except TypeError, ValueError:
        return "unknown"
    if not isinstance(decoded, dict):
        return "unknown"
    compaction = decoded.get("compaction")
    reason = compaction.get("reason") if isinstance(compaction, dict) else None
    return reason if isinstance(reason, str) else "unknown"


def _trigger_index(input_items: list[Any]) -> int | None:
    indices = [
        index
        for index, item in enumerate(input_items)
        if isinstance(item, dict) and item.get("type") == COMPACTION_TRIGGER_TYPE
    ]
    if not indices:
        return None
    if len(indices) != 1 or indices[0] != len(input_items) - 1:
        raise InvalidCodexCompactionRequest(
            "Codex Remote Compaction V2 requires exactly one final compaction_trigger"
        )
    return indices[0]


def _replacement_input(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
    }


def _rehydrate_input(
    input_items: list[Any],
    *,
    route: ResolvedRoute,
    persistence: Any | None,
    principal_id: str,
    now: datetime,
) -> tuple[list[Any], int, int, int]:
    """Expand owned Rosetta handles and silently discard unusable items."""
    result: list[Any] = []
    rehydrated = 0
    dropped_rosetta = 0
    dropped_native = 0
    renew_at = _iso(now + COMPACTION_TTL)
    now_text = _iso(now)
    direct_responses = is_responses_passthrough(route)
    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != COMPACTION_ITEM_TYPE:
            result.append(item)
            continue
        encrypted_content = item.get("encrypted_content")
        if _is_rosetta_token(encrypted_content):
            mapping = (
                persistence.get_codex_compaction_mapping(
                    principal_id=principal_id,
                    token_hash=_token_hash(encrypted_content),
                    now=now_text,
                    renewed_expires_at=renew_at,
                )
                if persistence is not None
                else None
            )
            if mapping is None:
                dropped_rosetta += 1
            else:
                result.append(_replacement_input(mapping["replacement_text"]))
                rehydrated += 1
            continue
        if direct_responses:
            result.append(item)
        else:
            dropped_native += 1
    return result, rehydrated, dropped_rosetta, dropped_native


def _summary_request(body: dict[str, Any], input_items: list[Any]) -> dict[str, Any]:
    request = copy.deepcopy(body)
    history = [
        item
        for item in input_items
        if not isinstance(item, dict) or item.get("type") != "additional_tools"
    ]
    request["input"] = [*history, _replacement_input(COMPACT_PROMPT)]
    request["stream"] = False
    for field in (
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "additional_tools",
    ):
        request.pop(field, None)
    metadata = request.get("client_metadata")
    if isinstance(metadata, dict):
        metadata.pop("x-codex-turn-metadata", None)
        if not metadata:
            request.pop("client_metadata", None)
    return request


def prepare_codex_compaction(
    body: dict[str, Any],
    *,
    route: ResolvedRoute,
    persistence: Any | None,
    principal_id: str,
    now: datetime | None = None,
) -> CompactionPreparation:
    """Rehydrate history and select native passthrough or Rosetta summary."""
    request = copy.deepcopy(body)
    input_items = request.get("input")
    if not isinstance(input_items, list):
        return CompactionPreparation(request, None, "unknown", None, 0, 0, 0)
    # Validate the client-visible sequence before replay can silently discard
    # obsolete compaction items.
    has_trigger = _trigger_index(input_items) is not None
    current_time = now or _utc_now()
    restored, rehydrated, dropped_rosetta, dropped_native = _rehydrate_input(
        input_items,
        route=route,
        persistence=persistence,
        principal_id=principal_id,
        now=current_time,
    )
    request["input"] = restored
    if not has_trigger:
        return CompactionPreparation(
            request,
            None,
            "unknown",
            None,
            rehydrated,
            dropped_rosetta,
            dropped_native,
        )
    reason = _compaction_reason(request)
    mode = (
        "native"
        if is_responses_passthrough(route) and reason in NATIVE_COMPACTION_REASONS
        else "rosetta"
    )
    if mode == "native":
        return CompactionPreparation(
            request,
            mode,
            reason,
            None,
            rehydrated,
            dropped_rosetta,
            dropped_native,
        )
    return CompactionPreparation(
        request,
        mode,
        reason,
        _summary_request(request, restored[:-1]),
        rehydrated,
        dropped_rosetta,
        dropped_native,
    )


def _assistant_text_from_item(item: dict[str, Any]) -> str:
    if item.get("type") != "message" or item.get("role") != "assistant":
        return ""
    content = item.get("content")
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"output_text", "text"} and isinstance(
            part.get("text"), str
        ):
            texts.append(part["text"])
    return "".join(texts)


def extract_assistant_summary(response: dict[str, Any]) -> str:
    """Accept one or more assistant messages, but reject tool output and empties."""
    output = response.get("output")
    if not isinstance(output, list):
        raise InvalidCompactionSummary("internal compaction response has no output")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {"function_call", "custom_tool_call", "computer_call"}:
            raise InvalidCompactionSummary(
                "internal compaction response returned a tool call"
            )
        texts.append(_assistant_text_from_item(item))
    summary = "".join(texts).strip()
    if not summary:
        raise InvalidCompactionSummary(
            "internal compaction response did not contain assistant text"
        )
    return summary


def create_compaction_mapping(
    persistence: Any,
    *,
    principal_id: str,
    source_model: str,
    reason: str,
    summary: str,
    now: datetime | None = None,
) -> CreatedCompactionMapping:
    """Store the full historical replacement and return its opaque handle."""
    created = now or _utc_now()
    token = f"{ROSETTA_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    replacement = f"{SUMMARY_PREFIX}\n\n{summary}"
    persistence.store_codex_compaction_mapping(
        principal_id=principal_id,
        token_hash=_token_hash(token),
        replacement_text=replacement,
        source_model=source_model,
        reason=reason,
        prompt_sha256=COMPACT_PROMPT_SHA256,
        created_at=_iso(created),
        expires_at=_iso(created + COMPACTION_TTL),
    )
    return CreatedCompactionMapping(token=token, prompt_sha256=COMPACT_PROMPT_SHA256)


def _compaction_item(token: str) -> dict[str, Any]:
    return {
        "type": COMPACTION_ITEM_TYPE,
        "id": f"comp_{uuid.uuid4().hex}",
        "encrypted_content": token,
    }


def _response(model: str, item: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "status": status,
        "output": [] if status == "in_progress" else [item],
        "usage": None,
    }


async def _compaction_sse(model: str, item: dict[str, Any]):
    formatter = SSE_FORMATTERS["openai_responses"]
    created = _response(model, item, "in_progress")
    completed = _response(model, item, "completed")
    completed["id"] = created["id"]
    for event in (
        {"type": "response.created", "response": created},
        {"type": "response.output_item.added", "output_index": 0, "item": item},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": completed},
    ):
        yield formatter(event)


def build_compaction_response(
    *, model: str, token: str, stream: bool
) -> Response | StreamingResponse:
    """Build the canonical non-streaming or four-event SSE V2 response."""
    item = _compaction_item(token)
    if stream:
        return StreamingResponse(
            _compaction_sse(model, item), content_type="text/event-stream"
        )
    return JSONResponse(_response(model, item, "completed"))
