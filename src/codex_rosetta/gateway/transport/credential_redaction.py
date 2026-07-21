"""Provider-return credential redaction at the transport boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.observability.redaction import SecretRedactor

from ._base import (
    UpstreamConnectionError,
    UpstreamResponse,
    UpstreamStream,
    UpstreamTransport,
)
from .provider_info import ProviderInfo


def _provider_redactor(provider_info: ProviderInfo) -> SecretRedactor:
    values = getattr(provider_info, "credential_values", ())
    if not isinstance(values, tuple | list | set | frozenset):
        values = ()
    return SecretRedactor(values)


def _sanitized_transport_error(
    provider_info: ProviderInfo,
    exc: Exception,
) -> UpstreamConnectionError:
    """Return a cause-free transport error whose message contains no provider key."""
    message = _provider_redactor(provider_info).redact_exact(str(exc))
    error_type = type(exc) if isinstance(exc, UpstreamConnectionError) else None
    try:
        error = error_type(message) if error_type is not None else None
    except TypeError:
        error = None
    if not isinstance(error, UpstreamConnectionError):
        error = UpstreamConnectionError(message)
    error.__cause__ = None
    error.__context__ = None
    return error


class CredentialRedactingStream(UpstreamStream):
    """Sanitize one upstream stream before any consumer can inspect it."""

    def __init__(self, stream: UpstreamStream, provider_info: ProviderInfo) -> None:
        self._stream = stream
        self._provider_info = provider_info
        self._redactor = _provider_redactor(provider_info)

    @property
    def status_code(self) -> int:
        return self._stream.status_code

    async def __aenter__(self) -> CredentialRedactingStream:
        error: UpstreamConnectionError | None = None
        try:
            await self._stream.__aenter__()
        except Exception as exc:
            error = _sanitized_transport_error(self._provider_info, exc)
        if error is not None:
            raise error from None
        return self

    async def __aexit__(self, *args: Any) -> None:
        error: UpstreamConnectionError | None = None
        try:
            await self._stream.__aexit__(*args)
        except Exception as exc:
            error = _sanitized_transport_error(self._provider_info, exc)
        if error is not None:
            raise error from None

    async def read_error(self) -> str:
        error: UpstreamConnectionError | None = None
        try:
            value = await self._stream.read_error()
        except Exception as exc:
            error = _sanitized_transport_error(self._provider_info, exc)
            value = ""
        if error is not None:
            raise error from None
        return self._redactor.redact_wire_bytes(value.encode("utf-8")).decode("utf-8")

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def redacted_events() -> AsyncIterator[dict[str, Any]]:
            error: UpstreamConnectionError | None = None
            try:
                async for event in self._stream:
                    yield self._redactor.redact_exact(event)
            except asyncio.CancelledError, GeneratorExit:
                raise
            except Exception as exc:
                error = _sanitized_transport_error(self._provider_info, exc)
            if error is not None:
                raise error from None

        return redacted_events()

    def aiter_raw_bytes(self) -> AsyncIterator[bytes] | None:
        raw_stream = self._stream.aiter_raw_bytes()
        if raw_stream is None:
            return None

        async def redacted_bytes() -> AsyncIterator[bytes]:
            redactor = self._redactor.streaming_redactor()
            error: UpstreamConnectionError | None = None
            try:
                async for chunk in raw_stream:
                    safe = redactor.feed(chunk)
                    if safe:
                        yield safe
                tail = redactor.finish()
                if tail:
                    yield tail
            except asyncio.CancelledError, GeneratorExit:
                raise
            except Exception as exc:
                error = _sanitized_transport_error(self._provider_info, exc)
            if error is not None:
                raise error from None

        return redacted_bytes()

    async def close(self) -> None:
        error: UpstreamConnectionError | None = None
        try:
            close = getattr(self._stream, "close", None)
            if close is None:
                await self._stream.__aexit__(None, None, None)
            else:
                await close()
        except asyncio.CancelledError, GeneratorExit:
            raise
        except Exception as exc:
            error = _sanitized_transport_error(self._provider_info, exc)
        if error is not None:
            raise error from None


class CredentialRedactingTransport:
    """Decorate an upstream transport with provider-return credential removal."""

    def __init__(self, transport: UpstreamTransport) -> None:
        self._transport = transport

    @classmethod
    def wrap(cls, transport: UpstreamTransport) -> CredentialRedactingTransport:
        """Return one redacting layer around *transport*."""
        return transport if isinstance(transport, cls) else cls(transport)

    async def send_request(
        self,
        provider_info: ProviderInfo,
        target_provider: ProviderType,
        body: dict[str, Any],
        model: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> UpstreamResponse:
        error: UpstreamConnectionError | None = None
        try:
            response = await self._transport.send_request(
                provider_info,
                target_provider,
                body,
                model,
                extra_headers=extra_headers,
            )
        except Exception as exc:
            error = _sanitized_transport_error(provider_info, exc)
            response = UpstreamResponse(status_code=500, body=None, raw_content=b"")
        if error is not None:
            raise error from None
        return self._redact_response(provider_info, response)

    async def send_streaming(
        self,
        provider_info: ProviderInfo,
        target_provider: ProviderType,
        body: dict[str, Any],
        model: str,
        *,
        extra_headers: dict[str, str] | None = None,
        wire_body: bytes | None = None,
        wire_headers: dict[str, str] | None = None,
    ) -> UpstreamStream:
        kwargs: dict[str, Any] = {"extra_headers": extra_headers}
        if wire_body is not None:
            kwargs.update(wire_body=wire_body, wire_headers=wire_headers)
        error: UpstreamConnectionError | None = None
        try:
            stream = await self._transport.send_streaming(
                provider_info,
                target_provider,
                body,
                model,
                **kwargs,
            )
        except Exception as exc:
            error = _sanitized_transport_error(provider_info, exc)
            stream = None
        if error is not None:
            raise error from None
        assert stream is not None
        return CredentialRedactingStream(stream, provider_info)

    async def send_passthrough(
        self,
        provider_info: ProviderInfo,
        url: str,
        body: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> UpstreamResponse:
        error: UpstreamConnectionError | None = None
        try:
            response = await self._transport.send_passthrough(
                provider_info,
                url,
                body,
                extra_headers=extra_headers,
            )
        except Exception as exc:
            error = _sanitized_transport_error(provider_info, exc)
            response = UpstreamResponse(status_code=500, body=None, raw_content=b"")
        if error is not None:
            raise error from None
        return self._redact_response(provider_info, response)

    async def close(self) -> None:
        await self._transport.close()

    @staticmethod
    def _redact_response(
        provider_info: ProviderInfo,
        response: UpstreamResponse,
    ) -> UpstreamResponse:
        redactor = _provider_redactor(provider_info)
        return UpstreamResponse(
            status_code=response.status_code,
            body=redactor.redact_exact(response.body),
            raw_content=redactor.redact_wire_bytes(response.raw_content),
        )


__all__ = ["CredentialRedactingStream", "CredentialRedactingTransport"]
