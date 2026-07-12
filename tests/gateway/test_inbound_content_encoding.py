from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from compression import zstd

from codex_rosetta.gateway.inbound_content_encoding import (
    ZstdRequestBodyError,
    ZstdRequestBodyTooLargeError,
    decode_inbound_zstd,
    decompress_zstd_bounded,
)


def _request(body: bytes, *, encoding: str | None, limit: int = 1024):
    headers = {"content-length": str(len(body))}
    if encoding is not None:
        headers["content-encoding"] = encoding
    return SimpleNamespace(
        path="/v1/responses",
        body=body,
        headers=headers,
        app=SimpleNamespace(max_body_size=limit),
    )


def test_bounded_zstd_decode_accepts_output_at_limit() -> None:
    body = b"a" * 32
    assert decompress_zstd_bounded(zstd.compress(body), max_body_size=32) == body


def test_bounded_zstd_decode_rejects_output_above_limit() -> None:
    with pytest.raises(
        ZstdRequestBodyTooLargeError,
        match="too large after Zstd decompression",
    ):
        decompress_zstd_bounded(zstd.compress(b"a" * 33), max_body_size=32)


@pytest.mark.parametrize("body", [b"", b"not-zstd", zstd.compress(b"{}") + b"tail"])
def test_bounded_zstd_decode_rejects_malformed_frames(body: bytes) -> None:
    with pytest.raises(ZstdRequestBodyError):
        decompress_zstd_bounded(body, max_body_size=1024)


def test_inbound_zstd_hook_decodes_body_and_normalizes_headers() -> None:
    original = json.dumps({"model": "gpt-test"}).encode()
    request = _request(zstd.compress(original), encoding="zstd")

    assert decode_inbound_zstd(request) is None
    assert request.body == original
    assert request.headers.get("content-encoding") is None
    assert request.headers["content-length"] == str(len(original))


def test_inbound_zstd_hook_returns_413_for_decoded_overflow() -> None:
    request = _request(zstd.compress(b"a" * 33), encoding="zstd", limit=32)

    response = decode_inbound_zstd(request)

    assert response is not None
    assert response.status_code == 413
    assert b"too large after Zstd decompression" in response.body


def test_inbound_zstd_hook_returns_400_for_malformed_body() -> None:
    request = _request(b"not-zstd", encoding="zstd")

    response = decode_inbound_zstd(request)

    assert response is not None
    assert response.status_code == 400
    assert b"Invalid Zstd request body" in response.body


def test_inbound_zstd_hook_leaves_uncompressed_request_unchanged() -> None:
    body = b'{"model":"gpt-test"}'
    request = _request(body, encoding=None)

    assert decode_inbound_zstd(request) is None
    assert request.body is body
    assert request.headers == {
        "content-length": str(len(body)),
    }


def test_inbound_zstd_hook_does_not_decode_non_api_routes() -> None:
    body = zstd.compress(b"{}")
    request = _request(body, encoding="zstd")
    request.path = "/admin/api/config"

    assert decode_inbound_zstd(request) is None
    assert request.body is body
    assert request.headers["content-encoding"] == "zstd"
