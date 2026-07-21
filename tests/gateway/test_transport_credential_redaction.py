"""Provider credential removal at the abstract transport boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from codex_rosetta.gateway.transport._base import (
    UpstreamConnectionError,
    UpstreamCredentialCollisionError,
    UpstreamResponse,
    UpstreamStream,
)
from codex_rosetta.gateway.transport.credential_redaction import (
    CredentialRedactingTransport,
)
from codex_rosetta.gateway.transport.provider_info import ProviderInfo, openai_auth


def _provider(keys: str = "first-key, prefix, prefix-long, final-key") -> ProviderInfo:
    return ProviderInfo(
        "test",
        api_key=keys,
        base_url="https://upstream.example/v1",
        auth_header_fn=openai_auth,
        url_template="{base_url}/responses",
    )


def _active_key(provider_info: ProviderInfo) -> str:
    return provider_info.auth_headers()["Authorization"].removeprefix("Bearer ")


class _ReflectingTransport:
    async def send_request(
        self,
        provider_info: ProviderInfo,
        target_provider: str,
        body: dict[str, Any],
        model: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> UpstreamResponse:
        del target_provider, model, extra_headers
        token = _active_key(provider_info)
        status = int(body.get("status", 200))
        payload = {
            "nested": [{"message": f"stable-before {token} stable-after"}],
            "status": status,
        }
        return UpstreamResponse(
            status_code=status,
            body=payload if status < 400 else None,
            raw_content=json.dumps(payload, separators=(",", ":")).encode(),
        )

    async def send_passthrough(
        self,
        provider_info: ProviderInfo,
        url: str,
        body: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> UpstreamResponse:
        del url
        return await self.send_request(
            provider_info,
            "openai_responses",
            body,
            "test",
            extra_headers=extra_headers,
        )

    async def send_streaming(self, *args: Any, **kwargs: Any) -> UpstreamStream:
        raise AssertionError("not used")

    async def close(self) -> None:
        return None


def test_non_streaming_blocks_every_rotation_position_on_success_and_error():
    provider = _provider()
    transport = CredentialRedactingTransport.wrap(_ReflectingTransport())

    async def run() -> None:
        for status in (200, 401, 200, 429):
            with pytest.raises(UpstreamCredentialCollisionError) as caught:
                await transport.send_request(
                    provider,
                    "openai_responses",
                    {"status": status},
                    "test",
                )
            assert str(caught.value).endswith("response blocked")
            assert all(
                token not in str(caught.value) for token in provider.credential_values
            )

    asyncio.run(run())


def test_non_streaming_ignores_credentials_outside_active_provider() -> None:
    unrelated_credential = "provider-b-secret"

    class _OtherProviderCredentialTransport(_ReflectingTransport):
        async def send_request(self, *args: Any, **kwargs: Any) -> UpstreamResponse:
            payload = {"output": unrelated_credential}
            return UpstreamResponse(
                status_code=200,
                body=payload,
                raw_content=json.dumps(payload, separators=(",", ":")).encode(),
            )

    async def run() -> UpstreamResponse:
        return await CredentialRedactingTransport.wrap(
            _OtherProviderCredentialTransport()
        ).send_request(
            _provider("provider-a-secret"),
            "openai_responses",
            {},
            "test",
        )

    response = asyncio.run(run())

    assert response.body == {"output": unrelated_credential}
    assert response.raw_content == b'{"output":"provider-b-secret"}'


class _ErrorTransport(_ReflectingTransport):
    async def send_request(
        self, provider_info: ProviderInfo, *args: Any, **kwargs: Any
    ):
        token = _active_key(provider_info)
        try:
            raise ValueError(f"retained cause contains {token}")
        except ValueError as cause:
            raise UpstreamConnectionError(f"request failed for {token}") from cause


def test_transport_exception_is_redacted_and_detached_from_sensitive_cause():
    provider = _provider("transport-secret")
    transport = CredentialRedactingTransport.wrap(_ErrorTransport())

    with pytest.raises(UpstreamConnectionError) as caught:
        asyncio.run(
            transport.send_request(provider, "openai_responses", {}, "test-model")
        )

    assert str(caught.value) == "request failed for [REDACTED]"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


class _Stream(UpstreamStream):
    def __init__(
        self,
        *,
        chunks: list[bytes] | None = None,
        events: list[dict[str, Any]] | None = None,
        error: str = "",
        status_code: int = 200,
        failure: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.chunks = chunks
        self.events = events or []
        self.error = error
        self.failure = failure
        self.closed = False

    async def read_error(self) -> str:
        if self.failure is not None:
            raise self.failure
        return self.error

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def events() -> AsyncIterator[dict[str, Any]]:
            for event in self.events:
                yield event
            if self.failure is not None:
                raise self.failure

        return events()

    def aiter_raw_bytes(self) -> AsyncIterator[bytes] | None:
        if self.chunks is None:
            return None

        async def chunks() -> AsyncIterator[bytes]:
            for chunk in self.chunks or []:
                yield chunk
            if self.failure is not None:
                raise self.failure

        return chunks()

    async def close(self) -> None:
        self.closed = True


class _StreamingTransport(_ReflectingTransport):
    def __init__(self, stream: UpstreamStream) -> None:
        self.stream = stream

    async def send_streaming(self, *args: Any, **kwargs: Any) -> UpstreamStream:
        return self.stream


def test_stream_blocks_parsed_events_and_http_errors_without_leaking():
    token = "stream-secret"
    provider = _provider(token)

    async def run() -> None:
        parsed = await CredentialRedactingTransport.wrap(
            _StreamingTransport(
                _Stream(events=[{"nested": {"text": f"before {token} after"}}])
            )
        ).send_streaming(provider, "openai_responses", {}, "test")
        with pytest.raises(UpstreamCredentialCollisionError):
            _ = [event async for event in parsed]

        http_error = await CredentialRedactingTransport.wrap(
            _StreamingTransport(
                _Stream(error=f'{{"error":"{token}"}}', status_code=401)
            )
        ).send_streaming(provider, "openai_responses", {}, "test")
        assert http_error.status_code == 401
        safe_error = await http_error.read_error()
        assert token not in safe_error
        assert json.loads(safe_error)["error"]["message"].endswith("response blocked")

        failure = UpstreamConnectionError(f"stream disconnected near {token}")
        failed = await CredentialRedactingTransport.wrap(
            _StreamingTransport(_Stream(events=[], failure=failure))
        ).send_streaming(provider, "openai_responses", {}, "test")
        with pytest.raises(UpstreamConnectionError) as caught:
            _ = [event async for event in failed]
        assert token not in str(caught.value)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None

    asyncio.run(run())


def test_stream_http_error_blocks_json_escaped_credential() -> None:
    token = 'stream-"escaped\\credential'
    provider = _provider(token)
    error = json.dumps({"error": {"message": token}}, separators=(",", ":"))

    async def run() -> str:
        stream = await CredentialRedactingTransport.wrap(
            _StreamingTransport(_Stream(error=error, status_code=400))
        ).send_streaming(provider, "openai_responses", {}, "test")
        return await stream.read_error()

    blocked = asyncio.run(run())

    assert token not in blocked
    assert json.loads(blocked)["error"]["message"].endswith("response blocked")


@pytest.mark.parametrize(
    ("token", "error"),
    [
        ("secret", '{"error":"\\u0073ecret"}'),
        ("a/b", '{"error":"a\\/b"}'),
    ],
)
def test_stream_http_error_blocks_semantically_escaped_credential(
    token: str,
    error: str,
) -> None:
    async def run() -> str:
        stream = await CredentialRedactingTransport.wrap(
            _StreamingTransport(_Stream(error=error, status_code=400))
        ).send_streaming(_provider(token), "openai_responses", {}, "test")
        return await stream.read_error()

    blocked = asyncio.run(run())

    assert token not in blocked
    assert json.loads(blocked)["error"]["message"].endswith("response blocked")


def test_non_streaming_raw_json_blocks_semantically_escaped_credential() -> None:
    class _SemanticRawTransport(_ReflectingTransport):
        async def send_request(self, *args: Any, **kwargs: Any) -> UpstreamResponse:
            return UpstreamResponse(
                status_code=500,
                body=None,
                raw_content=b'{"error":"\\u0073ecret"}',
            )

    async def run() -> None:
        with pytest.raises(UpstreamCredentialCollisionError):
            await CredentialRedactingTransport.wrap(
                _SemanticRawTransport()
            ).send_request(_provider("secret"), "openai_responses", {}, "test")

    asyncio.run(run())


def test_raw_sse_collision_blocks_every_cross_chunk_split_without_leaking():
    token = b"raw-stream-secret"
    provider = _provider(token.decode())
    payload = b'event: response.output_text.delta\ndata: {"delta":"before '
    payload += token + b' after"}\n\n'

    async def run(chunks: list[bytes]) -> bytes:
        stream = await CredentialRedactingTransport.wrap(
            _StreamingTransport(_Stream(chunks=chunks))
        ).send_streaming(provider, "openai_responses", {}, "test")
        raw = stream.aiter_raw_bytes()
        assert raw is not None
        emitted = bytearray()
        with pytest.raises(UpstreamCredentialCollisionError):
            async for chunk in raw:
                emitted.extend(chunk)
        return bytes(emitted)

    token_start = payload.index(token)
    for offset in range(len(token) + 1):
        split = token_start + offset
        emitted = asyncio.run(run([payload[:split], payload[split:]]))
        assert token not in emitted
        assert payload.startswith(emitted)

    emitted = asyncio.run(run([bytes([value]) for value in payload]))
    assert token not in emitted
    assert payload.startswith(emitted)


@pytest.mark.parametrize(
    ("token", "encoded"),
    [
        ("secret", b"\\u0073ecret"),
        ("a/b", b"a\\/b"),
        ("emoji-\U0001f600", b"emoji-\\ud83d\\ude00"),
    ],
)
def test_raw_sse_blocks_semantically_escaped_credentials_across_all_splits(
    token: str,
    encoded: bytes,
) -> None:
    payload = b'event: message\ndata: {"value":"' + encoded + b'"}\n\n'

    async def run(chunks: list[bytes]) -> bytes:
        stream = await CredentialRedactingTransport.wrap(
            _StreamingTransport(_Stream(chunks=chunks))
        ).send_streaming(_provider(token), "openai_responses", {}, "test")
        raw = stream.aiter_raw_bytes()
        assert raw is not None
        emitted = bytearray()
        with pytest.raises(UpstreamCredentialCollisionError):
            async for chunk in raw:
                emitted.extend(chunk)
        return bytes(emitted)

    for split in range(len(payload) + 1):
        assert asyncio.run(run([payload[:split], payload[split:]])) == b""


def test_raw_sse_without_collision_is_byte_identical() -> None:
    payload = b'event: message\ndata: {"text":"ordinary output"}\n\n'

    async def run() -> bytes:
        stream = await CredentialRedactingTransport.wrap(
            _StreamingTransport(
                _Stream(chunks=[payload[:7], payload[7:19], payload[19:]])
            )
        ).send_streaming(_provider("unrelated-secret"), "openai_responses", {}, "test")
        raw = stream.aiter_raw_bytes()
        assert raw is not None
        return b"".join([chunk async for chunk in raw])

    assert asyncio.run(run()) == payload


@pytest.mark.parametrize("token", ["data", "a", "1"])
def test_short_or_common_credentials_fail_closed(token: str) -> None:
    redactor = CredentialRedactingTransport.wrap(
        _StreamingTransport(_Stream(events=[{"data": "value", "count": 1}]))
    )

    async def run() -> None:
        stream = await redactor.send_streaming(
            _provider(token), "openai_responses", {}, "test"
        )
        with pytest.raises(UpstreamCredentialCollisionError):
            _ = [event async for event in stream]

    asyncio.run(run())


@pytest.mark.parametrize("token", ["data", "event", '"'])
def test_short_wire_syntax_credential_fails_closed(token: str) -> None:
    payload = b'event: message\ndata: {"value":"ordinary"}\n\n'

    async def run() -> bytes:
        stream = await CredentialRedactingTransport.wrap(
            _StreamingTransport(_Stream(chunks=[payload]))
        ).send_streaming(_provider(token), "openai_responses", {}, "test")
        raw = stream.aiter_raw_bytes()
        assert raw is not None
        emitted = bytearray()
        with pytest.raises(UpstreamCredentialCollisionError):
            async for chunk in raw:
                emitted.extend(chunk)
        return bytes(emitted)

    assert asyncio.run(run()) == b""
