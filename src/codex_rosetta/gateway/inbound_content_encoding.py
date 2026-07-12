"""Inbound request content-decoding under the Gateway body-size envelope."""

from __future__ import annotations

from typing import Any

from compression import zstd

from codex_rosetta._vendor.httpserver import JSONResponse, Response

_DECOMPRESS_CHUNK_BYTES = 64 * 1024


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
    if not (request.path == "/v1" or request.path.startswith("/v1/")):
        return None
    if str(request.headers.get("content-encoding", "")).strip().lower() != "zstd":
        return None

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
    return None
