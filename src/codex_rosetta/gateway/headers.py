"""Helpers for gateway request header forwarding."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any


# Request IDs are correlation metadata, not payloads.  Match the existing Codex
# window-ID envelope while leaving ample room for UUID, ULID, and trace IDs.
MAX_REQUEST_ID_BYTES = 128

_CODEX_WIRE_HEADER_NAMES = {
    "accept": "Accept",
    "content-encoding": "Content-Encoding",
    "content-type": "Content-Type",
    "originator": "Originator",
    "session-id": "Session-Id",
    "thread-id": "Thread-Id",
    "x-client-request-id": "x-client-request-id",
    "x-codex-beta-features": "x-codex-beta-features",
    "x-codex-turn-metadata": "x-codex-turn-metadata",
    "x-codex-window-id": "x-codex-window-id",
    "x-oai-attestation": "x-oai-attestation",
    "x-openai-internal-codex-responses-lite": (
        "x-openai-internal-codex-responses-lite"
    ),
}


def generate_request_id() -> str:
    """Return a Gateway-owned visible-ASCII correlation identifier."""

    return str(uuid.uuid4())


def resolve_request_id(value: Any) -> str:
    """Validate an external request ID or generate one when it is absent."""

    if value is None:
        return generate_request_id()
    if (
        not isinstance(value, str)
        or not value
        or any(ord(char) < 0x21 or ord(char) > 0x7E for char in value)
    ):
        raise ValueError("'x-request-id' must be a non-empty visible ASCII string")
    if len(value) > MAX_REQUEST_ID_BYTES:
        raise ValueError(
            f"'x-request-id' must be at most {MAX_REQUEST_ID_BYTES} ASCII bytes"
        )
    return value


def build_upstream_extra_headers(request: Any, request_id: str) -> dict[str, str]:
    """Build the explicit request headers that may be forwarded upstream."""
    extra_headers: dict[str, str] = {}

    if request_id:
        extra_headers["x-request-id"] = request_id

    user_agent = request.headers.get("user-agent")
    if user_agent:
        extra_headers["User-Agent"] = user_agent

    or_version = request.headers.get("openresponses-version")
    if or_version:
        extra_headers["OpenResponses-Version"] = or_version

    return extra_headers


def build_codex_wire_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Select wire-bound Codex headers without forwarding client credentials."""

    normalized = {str(name).lower(): str(value) for name, value in headers.items()}
    return {
        output_name: normalized[input_name]
        for input_name, output_name in _CODEX_WIRE_HEADER_NAMES.items()
        if input_name in normalized
    }
