"""Targeted API-token redaction for persisted gateway diagnostics."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

REDACTED = "[REDACTED]"

_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _is_token_key(value: Any) -> bool:
    """Return whether a field explicitly carries an API/auth token."""
    normalized = _normalized_key(value)
    return (
        normalized == "authorization"
        or normalized == "token"
        or normalized.endswith("token")
        or normalized == "apikey"
        or normalized.endswith("apikey")
    )


def _iter_config_values(value: Any, key: Any = "") -> Iterable[tuple[Any, Any]]:
    """Yield nested config values together with their owning field name."""
    yield key, value
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            yield from _iter_config_values(child_value, child_key)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_config_values(item, key)


def _add_token(values: set[str], value: Any) -> None:
    """Add a resolved non-empty API token to the redaction set."""
    if isinstance(value, str) and value and "${" not in value:
        values.add(value)


def collect_token_values(config: Mapping[str, Any]) -> set[str]:
    """Collect configured Gateway/provider API tokens for exact-value redaction."""
    values: set[str] = set()
    for key, value in _iter_config_values(config):
        normalized = _normalized_key(key)
        if _is_token_key(key):
            _add_token(values, value)
        if normalized == "apikeys" and isinstance(value, Mapping):
            _add_token(values, value.get("key"))
        elif normalized == "apikeys":
            _add_token(values, value)
    return values


class SecretRedactor:
    """Redact configured API tokens, Bearer values, and token JSON fields."""

    def __init__(self, token_values: Iterable[str] = ()) -> None:
        self.update(token_values)

    def update(self, token_values: Iterable[str]) -> None:
        """Replace the in-memory set of exact API-token values."""
        self._token_values = tuple(
            sorted(
                {
                    value
                    for value in token_values
                    if isinstance(value, str) and value and "${" not in value
                },
                key=lambda value: (-len(value), value),
            )
        )
        wire_values: set[bytes] = set()
        for token in self._token_values:
            wire_values.add(token.encode("utf-8"))
            for ensure_ascii in (False, True):
                escaped = json.dumps(token, ensure_ascii=ensure_ascii)[1:-1]
                wire_values.add(escaped.encode("utf-8"))
        self._wire_token_values = tuple(
            sorted(wire_values, key=lambda value: (-len(value), value))
        )

    def redact(self, value: Any) -> Any:
        """Return a deep redacted copy while preserving non-secret content."""
        return self._redact(deepcopy(value))

    def redact_exact(self, value: Any) -> Any:
        """Redact configured values without applying diagnostic field heuristics."""
        return self._redact_exact(deepcopy(value))

    def redact_wire_bytes(self, value: bytes) -> bytes:
        """Redact configured values from wire bytes without decoding other bytes."""
        stream = self.streaming_redactor()
        return stream.feed(value) + stream.finish()

    def streaming_redactor(self) -> StreamingSecretRedactor:
        """Create an independent exact-value redactor for a chunked byte stream."""
        return StreamingSecretRedactor(self._wire_token_values)

    def _redact(self, value: Any, *, token_field: bool = False) -> Any:
        if token_field:
            return REDACTED
        if isinstance(value, str):
            redacted = _BEARER_RE.sub(r"\1[REDACTED]", value)
            for token in self._token_values:
                redacted = redacted.replace(token, REDACTED)
            return redacted
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
            return self._redact(text).encode("utf-8")
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                redacted_key = (
                    self._redact_exact(key) if isinstance(key, str | bytes) else key
                )
                # Deterministic collision semantics: as with a normal dict
                # comprehension, the later source item wins.
                redacted[redacted_key] = self._redact(
                    item,
                    token_field=_is_token_key(key),
                )
            function = redacted.get("function")
            if isinstance(function, dict):
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        parsed_arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        pass
                    else:
                        redacted_arguments = self._redact(parsed_arguments)
                        if redacted_arguments != parsed_arguments:
                            function["arguments"] = json.dumps(
                                redacted_arguments,
                                ensure_ascii=False,
                            )
            return redacted
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact(item) for item in value)
        return value

    def _redact_exact(self, value: Any) -> Any:
        if isinstance(value, str):
            for token in self._token_values:
                value = value.replace(token, REDACTED)
            return value
        if isinstance(value, bytes):
            return self.redact_wire_bytes(value)
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                redacted_key = (
                    self._redact_exact(key) if isinstance(key, str | bytes) else key
                )
                redacted[redacted_key] = self._redact_exact(item)
            return redacted
        if isinstance(value, list):
            return [self._redact_exact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact_exact(item) for item in value)
        return value


class StreamingSecretRedactor:
    """Redact exact byte patterns across arbitrary input chunk boundaries."""

    def __init__(self, patterns: Iterable[bytes]) -> None:
        self._patterns = tuple(pattern for pattern in patterns if pattern)
        self._max_pattern_len = max(map(len, self._patterns), default=0)
        self._pattern = (
            re.compile(b"|".join(re.escape(pattern) for pattern in self._patterns))
            if self._patterns
            else None
        )
        self._pending = b""
        self._finished = False

    def feed(self, chunk: bytes) -> bytes:
        """Consume one chunk and return bytes that can no longer start a secret."""
        if self._finished:
            raise RuntimeError("streaming redactor is already finished")
        if not self._patterns:
            return chunk

        data = self._pending + chunk
        safe_limit = len(data) - self._max_pattern_len + 1
        if safe_limit <= 0:
            self._pending = data
            return b""

        assert self._pattern is not None
        output: list[bytes] = []
        offset = 0
        while match := self._pattern.search(data, offset):
            if match.start() >= safe_limit:
                break
            output.append(data[offset : match.start()])
            output.append(REDACTED.encode("ascii"))
            offset = match.end()
            if offset >= safe_limit:
                break
        if offset < safe_limit:
            output.append(data[offset:safe_limit])
            offset = safe_limit
        self._pending = data[offset:]
        return b"".join(output)

    def finish(self) -> bytes:
        """Flush the final buffered suffix and prevent further input."""
        if self._finished:
            return b""
        self._finished = True
        if not self._patterns:
            return b""
        assert self._pattern is not None
        output = self._pattern.sub(REDACTED.encode("ascii"), self._pending)
        self._pending = b""
        return output
