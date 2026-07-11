"""Safe URL-image retrieval for Google GenAI request conversion."""

from __future__ import annotations

import ipaddress
import http.client
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any


DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


class ImageFetchError(ValueError):
    """Raised when a remote image cannot be fetched under the active policy."""


class ImageFetchTimeoutError(ImageFetchError):
    """Raised when the single image-fetch deadline expires."""


class ImageFetchCancelledError(ImageFetchError):
    """Raised when the owning Gateway request cancels blocking image I/O."""


class ImageFetchCancellation:
    """Thread-safe cooperative cancellation and active-resource closer set."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._closers: set[Callable[[], Any]] = set()

    @property
    def cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self._event.is_set()

    def register_closer(self, closer: Callable[[], Any]) -> None:
        """Register an active socket/response closer, closing immediately if cancelled."""
        with self._lock:
            if not self._event.is_set():
                self._closers.add(closer)
                return
        try:
            closer()
        except Exception:
            pass

    def unregister_closer(self, closer: Callable[[], Any]) -> None:
        """Remove a no-longer-active closer."""
        with self._lock:
            self._closers.discard(closer)

    def cancel(self) -> None:
        """Signal cancellation and close all currently registered resources."""
        self._event.set()
        with self._lock:
            closers = tuple(self._closers)
        for closer in closers:
            try:
                closer()
            except Exception:
                pass


def _remaining_seconds(
    deadline: float,
    cancellation: ImageFetchCancellation | None,
) -> float:
    if cancellation is not None and cancellation.cancelled:
        raise ImageFetchCancelledError("Image download was cancelled")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ImageFetchTimeoutError("Image download timed out")
    return remaining


@dataclass(frozen=True)
class ImageFetchPolicy:
    """Immutable network and response limits for one URL-image fetch.

    Args:
        proxy_url: Explicit proxy URL. ``None`` disables proxy use, including
            proxies inherited from the process environment.
        timeout_seconds: Total socket timeout passed to ``urllib``.
        max_bytes: Maximum accepted response-body size.
        max_redirects: Maximum number of redirects before failing closed.
    """

    proxy_url: str | None = None
    timeout_seconds: float = 30.0
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    max_redirects: int = 3
    cancellation: ImageFetchCancellation | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if self.max_redirects < 0:
            raise ValueError("max_redirects must be non-negative")


def _public_ip(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return parsed.is_global and not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )


def _resolve_public_addresses(
    host: str,
    port: int,
    *,
    deadline: float | None = None,
    cancellation: ImageFetchCancellation | None = None,
) -> list[tuple[Any, ...]]:
    if deadline is not None:
        _remaining_seconds(deadline, cancellation)
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise ImageFetchError("Image URL host could not be resolved") from None
    if not addresses or any(not _public_ip(str(item[4][0])) for item in addresses):
        raise ImageFetchError("Image URL host is not publicly routable")
    if deadline is not None:
        _remaining_seconds(deadline, cancellation)
    return addresses


def _validate_public_url(
    url: str,
    *,
    deadline: float | None = None,
    cancellation: ImageFetchCancellation | None = None,
) -> str:
    """Validate a URL and every resolved address without returning secrets."""
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except TypeError, ValueError:
        raise ImageFetchError("Image URL is invalid") from None

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ImageFetchError("Image URL scheme is not supported")
    if parsed.username is not None or parsed.password is not None:
        raise ImageFetchError("Image URL credentials are not allowed")
    if not parsed.hostname:
        raise ImageFetchError("Image URL host is missing")

    _resolve_public_addresses(
        parsed.hostname,
        port or (443 if parsed.scheme.lower() == "https" else 80),
        deadline=deadline,
        cancellation=cancellation,
    )

    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def _create_public_connection(
    address: tuple[str, int],
    timeout: Any = None,
    source_address: tuple[str, int] | None = None,
    *,
    deadline: float | None = None,
    cancellation: ImageFetchCancellation | None = None,
) -> socket.socket:
    """Resolve once, validate all answers, then connect to a numeric address."""
    host, port = address
    last_error: OSError | None = None
    for item in _resolve_public_addresses(
        host,
        port,
        deadline=deadline,
        cancellation=cancellation,
    ):
        sockaddr = item[4]
        try:
            connect_timeout = (
                _remaining_seconds(deadline, cancellation)
                if deadline is not None
                else timeout
            )
            return socket.create_connection(
                (str(sockaddr[0]), int(sockaddr[1])),
                connect_timeout,
                source_address,
            )
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ImageFetchError("Image URL host is not publicly routable")


class _DeadlineConnectionMixin:
    """Apply remaining budget and expose live sockets to cancellation."""

    _deadline: float | None
    _cancellation: ImageFetchCancellation | None

    if TYPE_CHECKING:

        def close(self) -> None: ...

    def _init_deadline(
        self,
        deadline: float | None,
        cancellation: ImageFetchCancellation | None,
    ) -> None:
        self._deadline = deadline
        self._cancellation = cancellation

    def _before_connect(self) -> None:
        if self._deadline is not None:
            self.timeout = _remaining_seconds(self._deadline, self._cancellation)

    def _after_connect(self) -> None:
        if self._cancellation is not None:
            self._cancellation.register_closer(self.close)

    def _before_close(self) -> None:
        if self._cancellation is not None:
            self._cancellation.unregister_closer(self.close)


class _DeadlineHTTPConnection(_DeadlineConnectionMixin, http.client.HTTPConnection):
    def __init__(
        self,
        *args: Any,
        deadline: float | None = None,
        cancellation: ImageFetchCancellation | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._init_deadline(deadline, cancellation)

    def connect(self) -> None:
        self._before_connect()
        http.client.HTTPConnection.connect(self)
        self._after_connect()

    def close(self) -> None:
        self._before_close()
        http.client.HTTPConnection.close(self)


class _DeadlineHTTPSConnection(_DeadlineConnectionMixin, http.client.HTTPSConnection):
    def __init__(
        self,
        *args: Any,
        deadline: float | None = None,
        cancellation: ImageFetchCancellation | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._init_deadline(deadline, cancellation)

    def connect(self) -> None:
        self._before_connect()
        http.client.HTTPSConnection.connect(self)
        self._after_connect()

    def close(self) -> None:
        self._before_close()
        http.client.HTTPConnection.close(self)


class _PinnedHTTPConnection(_DeadlineHTTPConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = lambda address, timeout=None, source_address=None: (
            _create_public_connection(
                address,
                timeout,
                source_address,
                deadline=self._deadline,
                cancellation=self._cancellation,
            )
        )


class _PinnedHTTPSConnection(_DeadlineHTTPSConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = lambda address, timeout=None, source_address=None: (
            _create_public_connection(
                address,
                timeout,
                source_address,
                deadline=self._deadline,
                cancellation=self._cancellation,
            )
        )


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(
        self,
        *,
        deadline: float | None = None,
        cancellation: ImageFetchCancellation | None = None,
    ) -> None:
        super().__init__()
        self._deadline = deadline
        self._cancellation = cancellation

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(
            _PinnedHTTPConnection,
            req,
            deadline=self._deadline,
            cancellation=self._cancellation,
        )


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(
        self,
        *,
        deadline: float | None = None,
        cancellation: ImageFetchCancellation | None = None,
    ) -> None:
        super().__init__()
        self._pinned_context = getattr(self, "_context")
        self._deadline = deadline
        self._cancellation = cancellation

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(
            _PinnedHTTPSConnection,
            req,
            context=self._pinned_context,
            deadline=self._deadline,
            cancellation=self._cancellation,
        )


class _DeadlineHTTPHandler(urllib.request.HTTPHandler):
    def __init__(
        self,
        *,
        deadline: float,
        cancellation: ImageFetchCancellation | None,
    ) -> None:
        super().__init__()
        self._deadline = deadline
        self._cancellation = cancellation

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(
            _DeadlineHTTPConnection,
            req,
            deadline=self._deadline,
            cancellation=self._cancellation,
        )


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(
        self,
        *,
        deadline: float,
        cancellation: ImageFetchCancellation | None,
    ) -> None:
        super().__init__()
        self._deadline = deadline
        self._cancellation = cancellation
        self._deadline_context = getattr(self, "_context")

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(
            _DeadlineHTTPSConnection,
            req,
            context=self._deadline_context,
            deadline=self._deadline,
            cancellation=self._cancellation,
        )


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalidate every redirect target before ``urllib`` follows it."""

    def __init__(
        self,
        max_redirects: int,
        *,
        deadline: float | None = None,
        cancellation: ImageFetchCancellation | None = None,
    ) -> None:
        super().__init__()
        self._max_redirects = max_redirects
        self._redirect_count = 0
        self._deadline = deadline
        self._cancellation = cancellation

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        self._redirect_count += 1
        if self._redirect_count > self._max_redirects:
            raise ImageFetchError("Image URL redirected too many times")
        safe_url = _validate_public_url(
            newurl,
            deadline=self._deadline,
            cancellation=self._cancellation,
        )
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


def _set_response_timeout(response: Any, timeout: float) -> None:
    """Best-effort update of urllib's active socket timeout."""
    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is not None:
        sock.settimeout(timeout)


def _read_limited(
    response: Any,
    max_bytes: int,
    *,
    deadline: float,
    cancellation: ImageFetchCancellation | None,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    reader = getattr(response, "read1", None)
    if not callable(reader):
        reader = response.read
    while total <= max_bytes:
        remaining = _remaining_seconds(deadline, cancellation)
        _set_response_timeout(response, remaining)
        chunk = reader(min(_READ_CHUNK_BYTES, max_bytes + 1 - total))
        _remaining_seconds(deadline, cancellation)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > max_bytes:
        raise ImageFetchError("Image response is too large")
    return b"".join(chunks)


def fetch_image_url(
    url: str,
    policy: ImageFetchPolicy | None = None,
) -> tuple[bytes, str]:
    """Fetch one public HTTP(S) image under a bounded egress policy.

    Args:
        url: Untrusted image URL supplied by a conversion caller.
        policy: Per-call network and response limits.

    Returns:
        A ``(body, media_type)`` tuple.

    Raises:
        ImageFetchError: If URL, redirect, network, MIME, or size validation
            fails. Error messages never include the requested URL or body.
    """
    active_policy = policy or ImageFetchPolicy()
    deadline = time.monotonic() + active_policy.timeout_seconds
    cancellation = active_policy.cancellation
    safe_url = _validate_public_url(
        url,
        deadline=deadline,
        cancellation=cancellation,
    )
    proxy_handler = urllib.request.ProxyHandler(
        {
            "http": active_policy.proxy_url,
            "https": active_policy.proxy_url,
        }
        if active_policy.proxy_url
        else {}
    )
    handlers: list[Any] = [
        proxy_handler,
        _SafeRedirectHandler(
            active_policy.max_redirects,
            deadline=deadline,
            cancellation=cancellation,
        ),
    ]
    if active_policy.proxy_url is None:
        # Pin direct connections to the addresses that passed validation. An
        # explicitly configured proxy is a separate trusted DNS/egress owner.
        handlers.extend(
            (
                _PinnedHTTPHandler(
                    deadline=deadline,
                    cancellation=cancellation,
                ),
                _PinnedHTTPSHandler(
                    deadline=deadline,
                    cancellation=cancellation,
                ),
            )
        )
    else:
        handlers.extend(
            (
                _DeadlineHTTPHandler(
                    deadline=deadline,
                    cancellation=cancellation,
                ),
                _DeadlineHTTPSHandler(
                    deadline=deadline,
                    cancellation=cancellation,
                ),
            )
        )
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        safe_url,
        headers={"User-Agent": "codex-rosetta/1.0 (image fetch)"},
    )

    response = None
    try:
        response = opener.open(
            request,
            timeout=_remaining_seconds(deadline, cancellation),
        )
        _remaining_seconds(deadline, cancellation)
        if cancellation is not None:
            cancellation.register_closer(response.close)
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > active_policy.max_bytes:
                    raise ImageFetchError("Image response is too large")
            except ValueError:
                pass

        content_type = response.headers.get("Content-Type", "")
        media_type = content_type.partition(";")[0].strip().lower()
        if not media_type.startswith("image/"):
            raise ImageFetchError("Image response content type is not an image")
        return (
            _read_limited(
                response,
                active_policy.max_bytes,
                deadline=deadline,
                cancellation=cancellation,
            ),
            media_type,
        )
    except ImageFetchError:
        raise
    except OSError, urllib.error.URLError, ValueError:
        if cancellation is not None and cancellation.cancelled:
            raise ImageFetchCancelledError("Image download was cancelled") from None
        if time.monotonic() >= deadline:
            raise ImageFetchTimeoutError("Image download timed out") from None
        raise ImageFetchError("Image download failed") from None
    finally:
        if response is not None:
            if cancellation is not None:
                cancellation.unregister_closer(response.close)
            response.close()
