"""Authenticated client contract for the optional ``web-run`` browser sidecar."""

from __future__ import annotations

from typing import Any, Protocol

from codex_rosetta._vendor.httpclient import AsyncClient
from codex_rosetta.observability.redaction import SecretRedactor

from .transport.http.transport import request_bounded_response
from .web_search import WebSearchSettings

_MAX_SIDECAR_RESPONSE_BYTES = 1_000_000


class WebRunSidecarError(RuntimeError):
    """Base error returned by the browser sidecar client."""


class WebRunSidecarInvalidRequest(WebRunSidecarError):
    """The browser operation or reference is invalid."""


class WebRunSidecarNotImplemented(WebRunSidecarError):
    """The browser operation is recognized but unavailable."""


class WebRunBrowserClient(Protocol):
    """Minimal browser/PDF executor consumed by the Codex search bridge."""

    async def execute(
        self,
        *,
        session_id: str,
        operation: str,
        arguments: dict[str, Any],
    ) -> str:
        """Execute one scoped operation and return model-visible text."""


class WebRunSearchClient(Protocol):
    """Minimal self-hosted search executor exposed by the sidecar."""

    async def search(
        self,
        query: str,
        *,
        settings: WebSearchSettings,
    ) -> dict[str, Any]:
        """Return a normalized search result object."""


class WebRunSidecarHTTPClient:
    """Bounded bearer-authenticated HTTP client for the optional sidecar."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 45.0,
        search_provider: str = "self_hosted_google",
    ) -> None:
        root = base_url.rstrip("/")
        self._execute_url = f"{root}/v1/execute"
        self._search_url = f"{root}/v1/search"
        self._token = token
        self._redactor = SecretRedactor((token,))
        self._timeout = timeout
        self._search_provider = search_provider

    async def execute(
        self,
        *,
        session_id: str,
        operation: str,
        arguments: dict[str, Any],
    ) -> str:
        payload = {
            "session_id": session_id,
            "operation": operation,
            "arguments": arguments,
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        request_error: str | None = None
        async with AsyncClient(timeout=self._timeout) as client:
            try:
                response = await request_bounded_response(
                    client,
                    "POST",
                    self._execute_url,
                    json=payload,
                    headers=headers,
                    max_success_bytes=_MAX_SIDECAR_RESPONSE_BYTES,
                    max_error_bytes=_MAX_SIDECAR_RESPONSE_BYTES,
                )
            except Exception as exc:
                request_error = self._redactor.redact_exact(str(exc))
                response = None
        if request_error is not None:
            raise WebRunSidecarError(
                f"web-run sidecar request failed: {request_error}"
            ) from None
        assert response is not None

        invalid_json = False
        try:
            body = self._redactor.redact_exact(response.json())
        except Exception:
            invalid_json = True
            body = None
        if invalid_json:
            raise WebRunSidecarError("web-run sidecar returned invalid JSON") from None
        if not isinstance(body, dict):
            raise WebRunSidecarError("web-run sidecar returned a non-object response")
        if response.status_code >= 400:
            message = _sidecar_error_message(body, response.status_code)
            if response.status_code in {400, 404, 422}:
                raise WebRunSidecarInvalidRequest(message)
            if response.status_code == 501:
                raise WebRunSidecarNotImplemented(message)
            raise WebRunSidecarError(message)
        output = body.get("output")
        if not isinstance(output, str):
            raise WebRunSidecarError(
                "web-run sidecar response is missing string 'output'"
            )
        return output

    async def search(
        self,
        query: str,
        *,
        settings: WebSearchSettings,
    ) -> dict[str, Any]:
        """Run a bounded self-hosted search inside the authenticated sidecar."""
        payload = {
            "provider": self._search_provider,
            "query": query,
            "max_results": settings.max_results,
            "include_domains": list(settings.include_domains),
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        request_error: str | None = None
        async with AsyncClient(timeout=self._timeout) as client:
            try:
                response = await request_bounded_response(
                    client,
                    "POST",
                    self._search_url,
                    json=payload,
                    headers=headers,
                    max_success_bytes=_MAX_SIDECAR_RESPONSE_BYTES,
                    max_error_bytes=_MAX_SIDECAR_RESPONSE_BYTES,
                )
            except Exception as exc:
                request_error = self._redactor.redact_exact(str(exc))
                response = None
        if request_error is not None:
            raise WebRunSidecarError(
                f"web-run sidecar search failed: {request_error}"
            ) from None
        assert response is not None

        invalid_json = False
        try:
            body = self._redactor.redact_exact(response.json())
        except Exception:
            invalid_json = True
            body = None
        if invalid_json:
            raise WebRunSidecarError("web-run sidecar returned invalid JSON") from None
        if not isinstance(body, dict):
            raise WebRunSidecarError("web-run sidecar returned a non-object response")
        if response.status_code >= 400:
            message = _sidecar_error_message(body, response.status_code)
            if response.status_code in {400, 404, 422}:
                raise WebRunSidecarInvalidRequest(message)
            if response.status_code == 501:
                raise WebRunSidecarNotImplemented(message)
            raise WebRunSidecarError(message)
        results = body.get("results")
        if not isinstance(results, list):
            raise WebRunSidecarError(
                "web-run sidecar search response is missing array 'results'"
            )
        return body


def _sidecar_error_message(body: dict[str, Any], status_code: int) -> str:
    detail = body.get("detail")
    if isinstance(detail, dict):
        detail = detail.get("message")
    if not isinstance(detail, str) or not detail.strip():
        detail = body.get("error")
    if not isinstance(detail, str) or not detail.strip():
        detail = f"web-run sidecar returned HTTP {status_code}"
    return detail.strip()


__all__ = [
    "WebRunBrowserClient",
    "WebRunSearchClient",
    "WebRunSidecarError",
    "WebRunSidecarHTTPClient",
    "WebRunSidecarInvalidRequest",
    "WebRunSidecarNotImplemented",
]
