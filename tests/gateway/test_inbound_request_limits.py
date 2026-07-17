"""Raw-socket regressions for the Gateway's pre-auth request envelope."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Any, cast

import pytest
from compression import zstd

from codex_rosetta.gateway.app import create_app
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.proxy import close_resources


def _config(request_body_limit_mb: int | str = 128) -> GatewayConfig:
    return GatewayConfig(
        {
            "providers": {
                "test-provider": {
                    "api_key": "sk-upstream-test",
                    "base_url": "https://api.example.test/v1",
                    "type": "openai",
                }
            },
            "model_groups": {
                "test": {
                    "provider": "test-provider",
                    "type": "llm",
                    "models": {"gpt-test": {}},
                }
            },
            "server": {
                "admin_password": "test-admin-password",
                "api_keys": [
                    {
                        "id": "socket-client",
                        "label": "Socket client",
                        "key": "test-gateway-key",
                    }
                ],
                "admin_cors_origins": ["https://admin.example"],
                "request_body_limit_mb": request_body_limit_mb,
            },
        }
    )


class _RunningGateway:
    """Run a real Gateway App on an isolated loopback socket."""

    def __init__(self, request_body_limit_mb: int | str = 128) -> None:
        self.app = create_app(_config(request_body_limit_mb))

        @self.app.post("/v1/socket-test")
        async def socket_test(request: Any) -> dict[str, int]:
            return {"size": len(request.body)}

        self.ready = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stop_event: asyncio.Event | None = None
        self.port: int | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.ready.wait(timeout=5)
        assert self.port is not None

    def _run(self) -> None:
        async def _start() -> None:
            self.loop = asyncio.get_running_loop()
            self.stop_event = asyncio.Event()
            server = await asyncio.start_server(
                self.app._handle_connection,
                "127.0.0.1",
                0,
            )
            self.app._server = server
            assert server.sockets
            self.port = server.sockets[0].getsockname()[1]
            self.ready.set()
            async with server:
                await self.stop_event.wait()

        asyncio.run(_start())

    @property
    def address(self) -> tuple[str, int]:
        assert self.port is not None
        return ("127.0.0.1", self.port)

    def close(self) -> None:
        if self.loop is not None and self.stop_event is not None:
            self.loop.call_soon_threadsafe(self.stop_event.set)
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()
        app = cast(Any, self.app)
        asyncio.run(
            close_resources(
                transport=app.transport,
                metadata_store=app.metadata_store,
                codex_tool_store=app.codex_tool_store,
                image_fetch_workers=app.image_fetch_workers,
            )
        )

    def __enter__(self) -> _RunningGateway:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _receive_all(sock: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            chunk = sock.recv(65536)
        except ConnectionResetError:
            return b"".join(chunks)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _exchange(address: tuple[str, int], request: bytes) -> bytes:
    with socket.create_connection(address, timeout=2) as sock:
        sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)
        return _receive_all(sock)


def _wait_for_parser_count(app: Any, expected: int) -> None:
    deadline = time.monotonic() + 2
    while app.active_request_parses != expected and time.monotonic() < deadline:
        time.sleep(0.01)
    assert app.active_request_parses == expected


def test_gateway_uses_default_body_limit_and_fixed_parser_budgets() -> None:
    with _RunningGateway() as running:
        assert running.app.max_body_size == 128 * 1024 * 1024
        assert running.app.request_line_timeout == 5.0
        assert running.app.header_timeout == 10.0
        assert running.app.body_timeout == 30.0
        assert running.app.max_concurrent_request_parses == 64


@pytest.mark.parametrize(
    "path,credential_header,origin,expected_cors",
    [
        (
            "/v1/responses",
            b"Authorization: Bearer invalid\r\n",
            b"Origin: https://browser.example\r\n",
            b"Access-Control-Allow-Origin: *",
        ),
        (
            "/admin/api/config",
            b"X-Admin-Token: invalid\r\n",
            b"Origin: https://admin.example\r\n",
            b"Access-Control-Allow-Origin: https://admin.example",
        ),
    ],
)
def test_invalid_credentials_reject_declared_large_body_before_consuming_it(
    path: str,
    credential_header: bytes,
    origin: bytes,
    expected_cors: bytes,
) -> None:
    with _RunningGateway() as running:
        started = time.monotonic()
        with socket.create_connection(running.address, timeout=2) as sock:
            sock.sendall(
                f"POST {path} HTTP/1.1\r\n".encode()
                + b"Host: localhost\r\n"
                + credential_header
                + origin
                + b"Content-Length: 50000000\r\n\r\n{"
            )
            response = _receive_all(sock)
        elapsed = time.monotonic() - started

        assert b" 401 " in response.split(b"\r\n", 1)[0]
        assert expected_cors in response
        assert elapsed < 1
        assert running.app.active_request_parses == 0


def test_valid_gateway_key_allows_body_read_and_dispatch() -> None:
    with _RunningGateway() as running:
        response = _exchange(
            running.address,
            b"POST /v1/socket-test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Authorization: Bearer test-gateway-key\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 2\r\n\r\n{}",
        )

        assert b" 200 " in response.split(b"\r\n", 1)[0]
        assert b'"size": 2' in response
        assert running.app.active_request_parses == 0


def test_authenticated_zstd_body_is_decoded_before_dispatch() -> None:
    with _RunningGateway() as running:
        body = zstd.compress(b"{}")
        response = _exchange(
            running.address,
            b"POST /v1/socket-test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Authorization: Bearer test-gateway-key\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Encoding: zstd\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body,
        )

        assert b" 200 " in response.split(b"\r\n", 1)[0]
        assert b'"size": 2' in response
        assert running.app.active_request_parses == 0


def test_invalid_gateway_key_is_rejected_before_zstd_decode() -> None:
    with _RunningGateway() as running:
        body = b"not-zstd"
        response = _exchange(
            running.address,
            b"POST /v1/socket-test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Authorization: Bearer invalid\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Encoding: zstd\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body,
        )

        assert b" 401 " in response.split(b"\r\n", 1)[0]
        assert b"invalid_api_key" in response
        assert b"Invalid Zstd request body" not in response
        assert running.app.active_request_parses == 0


def test_zstd_decoded_body_uses_same_live_limit_as_compressed_body() -> None:
    with _RunningGateway() as running:
        running.app.max_body_size = 32
        body = zstd.compress(b"a" * 33)
        assert len(body) <= running.app.max_body_size
        response = _exchange(
            running.address,
            b"POST /v1/socket-test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Authorization: Bearer test-gateway-key\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Encoding: zstd\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body,
        )

        assert b" 413 " in response.split(b"\r\n", 1)[0]
        assert b"too large after Zstd decompression" in response
        assert running.app.active_request_parses == 0


def test_zstd_compressed_body_uses_existing_live_limit_before_decode() -> None:
    with _RunningGateway() as running:
        running.app.max_body_size = 32
        response = _exchange(
            running.address,
            b"POST /v1/socket-test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Authorization: Bearer test-gateway-key\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Encoding: zstd\r\n"
            b"Content-Length: 33\r\n\r\n",
        )

        assert b" 413 " in response.split(b"\r\n", 1)[0]
        assert b"Request body too large (33 bytes)" in response
        assert running.app.active_request_parses == 0


def test_configured_body_limit_rejects_oversized_content_length() -> None:
    with _RunningGateway(request_body_limit_mb=64) as running:
        response = _exchange(
            running.address,
            b"POST /v1/socket-test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Authorization: Bearer test-gateway-key\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 67108865\r\n\r\n",
        )

        assert b" 413 " in response.split(b"\r\n", 1)[0]
        assert b"Request body too large (67108865 bytes)" in response
        assert running.app.active_request_parses == 0


def test_public_admin_login_body_is_read_under_body_deadline() -> None:
    with _RunningGateway() as running:
        running.app.body_timeout = 0.15
        with socket.create_connection(running.address, timeout=2) as sock:
            sock.sendall(
                b"POST /admin/api/login HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 4\r\n\r\n{"
            )
            _wait_for_parser_count(running.app, 1)
            response = _receive_all(sock)

        assert b" 408 " in response.split(b"\r\n", 1)[0]
        assert running.app.active_request_parses == 0


def test_slow_request_line_uses_gateway_phase_deadline() -> None:
    with _RunningGateway() as running:
        running.app.request_line_timeout = 0.15
        with socket.create_connection(running.address, timeout=2) as sock:
            for value in (b"G", b"E", b"T", b" "):
                try:
                    sock.sendall(value)
                except BrokenPipeError:
                    break
                time.sleep(0.06)
            response = _receive_all(sock)

        assert b" 408 " in response.split(b"\r\n", 1)[0]
        assert running.app.active_request_parses == 0


def test_parser_capacity_rejects_the_sixty_fifth_connection_without_waiting() -> None:
    with _RunningGateway() as running:
        held: list[socket.socket] = []
        try:
            for _index in range(64):
                held.append(socket.create_connection(running.address, timeout=2))
            _wait_for_parser_count(running.app, 64)

            started = time.monotonic()
            with socket.create_connection(running.address, timeout=2) as overflow:
                response = _receive_all(overflow)
            elapsed = time.monotonic() - started

            assert b" 503 " in response.split(b"\r\n", 1)[0]
            assert elapsed < 1
            assert running.app.active_request_parses == 64
        finally:
            for sock in held:
                sock.close()
            _wait_for_parser_count(running.app, 0)


@pytest.mark.parametrize(
    "raw_request,expected_origin",
    [
        (
            b"OPTIONS /v1/responses HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Origin: https://browser.example\r\n"
            b"Access-Control-Request-Method: POST\r\n"
            b"Access-Control-Request-Headers: authorization\r\n\r\n",
            b"Access-Control-Allow-Origin: *",
        ),
        (
            b"OPTIONS /admin/api/config HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Origin: https://admin.example\r\n"
            b"Access-Control-Request-Method: GET\r\n"
            b"Access-Control-Request-Headers: x-admin-token\r\n\r\n",
            b"Access-Control-Allow-Origin: https://admin.example",
        ),
    ],
)
def test_cors_preflight_remains_public_on_real_socket(
    raw_request: bytes,
    expected_origin: bytes,
) -> None:
    with _RunningGateway() as running:
        response = _exchange(running.address, raw_request)

        assert b" 204 " in response.split(b"\r\n", 1)[0]
        assert expected_origin in response
