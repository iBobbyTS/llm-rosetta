"""Bounded capture/fault proxy for real GPT relay compatibility tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from compression import zstd
except ImportError:  # pragma: no cover - Python < 3.14 is unsupported by this repo
    zstd = None

from codex_rosetta.gateway.config import load_config_raw

MAX_BODY_BYTES = 64 * 1024 * 1024
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _join_upstream(base_url: str, incoming_path: str) -> str:
    parsed = urlsplit(base_url.rstrip("/"))
    base_path = parsed.path.rstrip("/")
    path = incoming_path if incoming_path.startswith("/") else f"/{incoming_path}"
    if base_path.endswith("/v1") and path.startswith("/v1/"):
        path = path[3:]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{base_path}{path}", "", ""))


def _decode_request(body: bytes, encoding: str | None) -> bytes:
    if encoding and "zstd" in encoding.lower():
        if zstd is None:
            raise RuntimeError(
                "zstd request received but compression.zstd is unavailable"
            )
        return zstd.decompress(body)
    return body


def _encode_request(body: bytes, encoding: str | None) -> bytes:
    if encoding and "zstd" in encoding.lower():
        if zstd is None:
            raise RuntimeError(
                "zstd request received but compression.zstd is unavailable"
            )
        return zstd.compress(body)
    return body


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(
            _contains_key(child, key) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    return False


def summarize_request(
    body: bytes, encoding: str | None
) -> tuple[dict[str, Any], bytes]:
    """Return a prompt-free request summary and decoded body."""
    decoded = _decode_request(body, encoding)
    try:
        payload = json.loads(decoded)
    except UnicodeDecodeError, json.JSONDecodeError:
        return {
            "json": False,
            "body_sha256": hashlib.sha256(decoded).hexdigest(),
            "body_bytes": len(decoded),
        }, decoded
    input_types = []
    if isinstance(payload, dict) and isinstance(payload.get("input"), list):
        input_types = [
            item.get("type")
            for item in payload["input"]
            if isinstance(item, dict) and isinstance(item.get("type"), str)
        ]
    stream_options = (
        payload.get("stream_options") if isinstance(payload, dict) else None
    )
    return {
        "json": True,
        "top_level_keys": sorted(payload) if isinstance(payload, dict) else [],
        "model": payload.get("model") if isinstance(payload, dict) else None,
        "input_types": input_types,
        "has_internal_item_metadata": _contains_key(
            payload, "internal_chat_message_metadata_passthrough"
        ),
        "reasoning_summary_delivery": (
            stream_options.get("reasoning_summary_delivery")
            if isinstance(stream_options, dict)
            else None
        ),
        "body_sha256": hashlib.sha256(decoded).hexdigest(),
        "body_bytes": len(decoded),
    }, decoded


class CaptureState:
    def __init__(
        self,
        *,
        upstream_base_url: str,
        upstream_api_key: str,
        log_path: Path,
        actual_model: str,
        fail_old_model_compact: bool,
        normalize_zstd_upstream: bool,
    ) -> None:
        self.upstream_base_url = upstream_base_url
        self.upstream_api_key = upstream_api_key
        self.log_path = log_path
        self.actual_model = actual_model
        self.fail_old_model_compact = fail_old_model_compact
        self.normalize_zstd_upstream = normalize_zstd_upstream
        self._failed_old_model_compact = False
        self._sequence = 0
        self._lock = threading.Lock()

    def record(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._sequence += 1
            event = {"sequence": self._sequence, **event}
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
                )

    def should_fail_compact(
        self, requested_model: str | None, input_types: list[str]
    ) -> bool:
        with self._lock:
            should_fail = (
                self.fail_old_model_compact
                and not self._failed_old_model_compact
                and "compaction_trigger" in input_types
                and requested_model == "relay-probe-old"
            )
            if should_fail:
                self._failed_old_model_compact = True
            return should_fail


class CaptureHandler(BaseHTTPRequestHandler):
    server: CaptureServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802, C901 - linear proxy transaction
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length < 0 or content_length > MAX_BODY_BYTES:
            self.send_error(413)
            return
        raw_body = self.rfile.read(content_length)
        encoding = self.headers.get("Content-Encoding")
        try:
            summary, decoded = summarize_request(raw_body, encoding)
        except Exception as exc:
            self.server.state.record(
                {"kind": "request_decode_error", "path": self.path, "error": str(exc)}
            )
            self.send_error(400, "request decode failed")
            return

        requested_model = summary.get("model")
        self.server.state.record(
            {
                "kind": "request",
                "path": self.path,
                "content_encoding": encoding,
                "upstream_content_encoding": (
                    None
                    if self.server.state.normalize_zstd_upstream
                    and encoding
                    and "zstd" in encoding.lower()
                    else encoding
                ),
                "transport_adapted": bool(
                    self.server.state.normalize_zstd_upstream
                    and encoding
                    and "zstd" in encoding.lower()
                ),
                **summary,
            }
        )
        if self.server.state.should_fail_compact(
            requested_model, summary.get("input_types", [])
        ):
            body = json.dumps(
                {
                    "error": {
                        "message": "synthetic old-model compact rejection",
                        "type": "invalid_request_error",
                    }
                }
            ).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.server.state.record(
                {
                    "kind": "response",
                    "path": self.path,
                    "status": 400,
                    "synthetic": True,
                }
            )
            return

        forwarded_model = requested_model
        try:
            payload = json.loads(decoded)
            if isinstance(payload, dict) and requested_model == "relay-probe-old":
                payload["model"] = self.server.state.actual_model
                forwarded_model = self.server.state.actual_model
                decoded = json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ).encode()
                raw_body = _encode_request(decoded, encoding)
        except UnicodeDecodeError, json.JSONDecodeError:
            pass

        upstream_url = _join_upstream(self.server.state.upstream_base_url, self.path)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP
            and key.lower()
            not in {"host", "content-length", "authorization", "api-key"}
        }
        headers["Authorization"] = f"Bearer {self.server.state.upstream_api_key}"
        if (
            self.server.state.normalize_zstd_upstream
            and encoding
            and "zstd" in encoding.lower()
        ):
            raw_body = decoded
            headers.pop("Content-Encoding", None)
        request = urllib.request.Request(
            upstream_url, data=raw_body, headers=headers, method="POST"
        )
        try:
            response = urllib.request.urlopen(
                request, timeout=180, context=ssl.create_default_context()
            )
        except urllib.error.HTTPError as exc:
            response = exc
        except Exception as exc:
            self.server.state.record(
                {
                    "kind": "upstream_error",
                    "path": self.path,
                    "forwarded_model": forwarded_model,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            self.send_error(502, "upstream connection failed")
            return

        response_body = response.read(MAX_BODY_BYTES + 1)
        if len(response_body) > MAX_BODY_BYTES:
            self.send_error(502, "upstream response exceeded capture limit")
            return
        status = response.status
        content_type = response.headers.get("Content-Type", "")
        event_names: list[str] = []
        error_type = None
        error_code = None
        if status >= 400 and "application/json" in content_type:
            try:
                error_payload = json.loads(response_body)
            except UnicodeDecodeError, json.JSONDecodeError:
                error_payload = None
            if isinstance(error_payload, dict):
                error_value = error_payload.get("error")
                if isinstance(error_value, dict):
                    error_type = error_value.get("type")
                    error_code = error_value.get("code")
        if "text/event-stream" in content_type:
            for line in response_body.decode("utf-8", errors="replace").splitlines():
                if line.startswith("event: "):
                    event_names.append(line[7:].strip())
                elif line.startswith("data: ") and line[6:].strip() != "[DONE]":
                    try:
                        event = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and isinstance(event.get("type"), str):
                        event_names.append(event["type"])
        self.server.state.record(
            {
                "kind": "response",
                "path": self.path,
                "status": status,
                "synthetic": False,
                "forwarded_model": forwarded_model,
                "content_type": content_type.split(";", 1)[0],
                "event_names": event_names,
                "stream_completed": "response.completed" in event_names,
                "error_type": error_type,
                "error_code": error_code,
                "body_bytes": len(response_body),
            }
        )
        self.send_response(status)
        for key, value in response.headers.items():
            if key.lower() not in HOP_BY_HOP and key.lower() != "content-length":
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)


class CaptureServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], state: CaptureState) -> None:
        super().__init__(address, CaptureHandler)
        self.state = state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--provider-name", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--fail-old-model-compact", action="store_true")
    parser.add_argument("--normalize-zstd-upstream", action="store_true")
    args = parser.parse_args()
    raw = load_config_raw(str(args.config))
    provider = raw.get("providers", {}).get(args.provider_name)
    if not isinstance(provider, dict):
        parser.error(f"provider {args.provider_name!r} not found")
    api_key = provider.get("api_key")
    base_url = provider.get("base_url")
    if not isinstance(api_key, str) or not api_key:
        parser.error("selected provider has no api_key")
    if not isinstance(base_url, str) or not base_url.startswith(
        ("http://", "https://")
    ):
        parser.error("selected provider has no valid base_url")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    state = CaptureState(
        upstream_base_url=base_url,
        upstream_api_key=api_key,
        log_path=args.log,
        actual_model=args.model,
        fail_old_model_compact=args.fail_old_model_compact,
        normalize_zstd_upstream=args.normalize_zstd_upstream,
    )
    server = CaptureServer(("127.0.0.1", args.port), state)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
