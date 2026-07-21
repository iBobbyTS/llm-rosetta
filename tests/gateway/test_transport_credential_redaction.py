"""Provider credential removal at the abstract transport boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from codex_rosetta.gateway.transport._base import (
    UpstreamConnectionError,
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


def test_non_streaming_redacts_every_rotation_position_on_success_and_error():
    provider = _provider()
    transport = CredentialRedactingTransport.wrap(_ReflectingTransport())
    credentials = set(provider.credential_values)

    async def run() -> list[UpstreamResponse]:
        responses = []
        for status in (200, 401, 200, 429):
            responses.append(
                await transport.send_request(
                    provider,
                    "openai_responses",
                    {"status": status},
                    "test",
                )
            )
        return responses

    responses = asyncio.run(run())

    assert [response.status_code for response in responses] == [200, 401, 200, 429]
    for response in responses:
        rendered = repr(response.body) + response.raw_content.decode()
        assert credentials.isdisjoint(rendered.split())
        assert all(token not in rendered for token in credentials)
        assert "stable-before [REDACTED] stable-after" in rendered


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


def test_stream_redacts_parsed_events_http_errors_and_stream_exceptions():
    token = "stream-secret"
    provider = _provider(token)

    async def run() -> None:
        parsed = await CredentialRedactingTransport.wrap(
            _StreamingTransport(
                _Stream(events=[{"nested": {"text": f"before {token} after"}}])
            )
        ).send_streaming(provider, "openai_responses", {}, "test")
        assert [event async for event in parsed] == [
            {"nested": {"text": "before [REDACTED] after"}}
        ]

        http_error = await CredentialRedactingTransport.wrap(
            _StreamingTransport(
                _Stream(error=f'{{"error":"{token}"}}', status_code=401)
            )
        ).send_streaming(provider, "openai_responses", {}, "test")
        assert http_error.status_code == 401
        assert await http_error.read_error() == '{"error":"[REDACTED]"}'

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


def test_stream_http_error_redacts_json_escaped_credential() -> None:
    token = 'stream-"escaped\\credential'
    provider = _provider(token)
    error = json.dumps({"error": {"message": token}}, separators=(",", ":"))

    async def run() -> str:
        stream = await CredentialRedactingTransport.wrap(
            _StreamingTransport(_Stream(error=error, status_code=400))
        ).send_streaming(provider, "openai_responses", {}, "test")
        return await stream.read_error()

    redacted = asyncio.run(run())

    assert json.loads(redacted) == {"error": {"message": "[REDACTED]"}}


def test_raw_sse_redaction_handles_every_cross_chunk_split_without_other_changes():
    token = b"raw-stream-secret"
    provider = _provider(token.decode())
    payload = b'event: response.output_text.delta\ndata: {"delta":"before '
    payload += token + b' after"}\n\n'
    expected = payload.replace(token, b"[REDACTED]")

    async def run(chunks: list[bytes]) -> bytes:
        stream = await CredentialRedactingTransport.wrap(
            _StreamingTransport(_Stream(chunks=chunks))
        ).send_streaming(provider, "openai_responses", {}, "test")
        raw = stream.aiter_raw_bytes()
        assert raw is not None
        return b"".join([chunk async for chunk in raw])

    token_start = payload.index(token)
    for offset in range(len(token) + 1):
        split = token_start + offset
        assert asyncio.run(run([payload[:split], payload[split:]])) == expected

    assert asyncio.run(run([bytes([value]) for value in payload])) == expected
