"""Inbound request content-decoding under the Gateway body-size envelope."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any

from compression import zstd

from codex_rosetta._vendor.httpserver import JSONResponse, Response

from .headers import build_codex_wire_headers

_DECOMPRESS_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class InboundWireRequest:
    """Original attested request bytes and headers before ingress decoding."""

    body: bytes
    headers: dict[str, str]
    parsed_body: dict[str, Any] | None = None

    def with_parsed_body(self, body: dict[str, Any]) -> InboundWireRequest:
        """Bind the parsed ingress object used to detect later mutations."""

        return replace(self, parsed_body=body)

    def matches(self, body: dict[str, Any]) -> bool:
        """Return whether the routed request remains semantically unchanged."""

        return self.parsed_body is not None and self.parsed_body == body


_inbound_wire_request_var: ContextVar[InboundWireRequest | None] = ContextVar(
    "inbound_wire_request",
    default=None,
)


def take_inbound_wire_request() -> InboundWireRequest | None:
    """Take and clear the current request's captured attested wire envelope."""

    wire_request = _inbound_wire_request_var.get()
    _inbound_wire_request_var.set(None)
    return wire_request


def bind_inbound_wire_request(
    wire_request: InboundWireRequest | None,
    body: dict[str, Any],
) -> tuple[InboundWireRequest | None, dict[str, Any]]:
    """Bind parsed JSON while isolating later top-level gateway mutations."""
    if wire_request is None:
        return None, body
    return wire_request.with_parsed_body(body), dict(body)


def _capture_attested_wire_request(
    request: Any,
    body: bytes,
    *,
    headers: dict[str, str] | None = None,
) -> None:
    headers = headers or build_codex_wire_headers(request.headers)
    if headers.get("x-oai-attestation"):
        _inbound_wire_request_var.set(InboundWireRequest(body=body, headers=headers))


class ZstdRequestBodyError(ValueError):
    """Raised when an inbound Zstd request body is malformed."""


class ZstdRequestBodyTooLargeError(ValueError):
    """Raised when decoded request bytes exceed the configured body limit."""


def decompress_zstd_bounded(body: bytes, *, max_body_size: int) -> bytes:
    """Decode one Zstd frame without allowing output above ``max_body_size``."""
    if isinstance(max_body_size, bool) or not isinstance(max_body_size, int):
        raise TypeError("max_body_size must be an integer")
    if max_body_size <= 0:
        raise ValueError("max_body_size must be positive")

    decompressor = zstd.ZstdDecompressor()
    chunks: list[bytes] = []
    total = 0
    pending = body
    try:
        while True:
            remaining = max_body_size - total
            chunk = decompressor.decompress(
                pending,
                max_length=min(_DECOMPRESS_CHUNK_BYTES, remaining + 1),
            )
            pending = b""
            if chunk:
                total += len(chunk)
                if total > max_body_size:
                    raise ZstdRequestBodyTooLargeError(
                        "Request body too large after Zstd decompression"
                    )
                chunks.append(chunk)
            if decompressor.eof:
                if decompressor.unused_data:
                    raise ZstdRequestBodyError(
                        "Invalid Zstd request body: trailing data"
                    )
                return b"".join(chunks)
            if decompressor.needs_input:
                raise ZstdRequestBodyError("Invalid or incomplete Zstd request body")
    except ZstdRequestBodyTooLargeError:
        raise
    except ZstdRequestBodyError:
        raise
    except (EOFError, zstd.ZstdError) as exc:
        raise ZstdRequestBodyError("Invalid Zstd request body") from exc


def decode_inbound_zstd(request: Any) -> Response | None:
    """Decode an authenticated ``/v1`` Zstd request or return a stable error."""
    _inbound_wire_request_var.set(None)
    if not (request.path == "/v1" or request.path.startswith("/v1/")):
        return None
    encoding = str(request.headers.get("content-encoding", "")).strip().lower()
    if encoding != "zstd":
        if request.path == "/v1/responses" and encoding in {"", "identity"}:
            _capture_attested_wire_request(request, request.body)
        return None

    encoded = request.body
    wire_headers = build_codex_wire_headers(request.headers)
    max_body_size = request.app.max_body_size
    try:
        decoded = decompress_zstd_bounded(
            request.body,
            max_body_size=max_body_size,
        )
    except ZstdRequestBodyTooLargeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=413)
    except ZstdRequestBodyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    request.body = decoded
    request.headers.pop("content-encoding", None)
    request.headers["content-length"] = str(len(decoded))
    if request.path == "/v1/responses":
        _capture_attested_wire_request(request, encoded, headers=wire_headers)
    return None
