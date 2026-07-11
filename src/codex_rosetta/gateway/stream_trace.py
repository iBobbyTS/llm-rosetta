"""Optional JSONL diagnostics for gateway streaming conversions."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.observability.redaction import SecretRedactor

logger = logging.getLogger("codex-rosetta-gateway")

DEFAULT_MAX_CHARS = 20_000
DEFAULT_TRACE_PATH = "~/.config/codex-rosetta-gateway/log.jsonl"


@dataclass
class StreamTraceConfig:
    """Runtime configuration for optional stream trace logging."""

    enabled: bool = False
    filter: str = ""
    path: str = ""
    max_string_chars: int = DEFAULT_MAX_CHARS

    @classmethod
    def from_mapping(cls, value: Any) -> StreamTraceConfig:
        """Build a trace config from ``server.stream_trace`` config data."""
        if not isinstance(value, dict):
            return cls()

        try:
            max_string_chars = int(value.get("max_string_chars", DEFAULT_MAX_CHARS))
        except TypeError, ValueError:
            max_string_chars = DEFAULT_MAX_CHARS
        if max_string_chars <= 0:
            max_string_chars = DEFAULT_MAX_CHARS

        return cls(
            enabled=bool(value.get("enabled", False)),
            filter=str(value.get("filter", "") or ""),
            path=str(value.get("path", "") or "").strip(),
            max_string_chars=max_string_chars,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable config dict."""
        return {
            "enabled": self.enabled,
            "filter": self.filter,
            "path": self.path,
            "max_string_chars": self.max_string_chars,
        }


@dataclass(frozen=True)
class PreparedStreamTraceUpdate:
    """Fully constructed trace state ready for an assignment-only commit."""

    config: StreamTraceConfig
    redactor: SecretRedactor


class StreamTraceState:
    """Mutable stream trace settings used by the running gateway."""

    def __init__(
        self,
        config: StreamTraceConfig | None = None,
        *,
        token_values: Iterable[str] = (),
    ) -> None:
        self.config = config or StreamTraceConfig()
        self._redactor = SecretRedactor(token_values)

    def prepare_update(
        self,
        config: StreamTraceConfig,
        *,
        token_values: Iterable[str] | None = None,
    ) -> PreparedStreamTraceUpdate:
        """Construct replacement trace state without mutating live settings."""
        redactor = (
            SecretRedactor(token_values) if token_values is not None else self._redactor
        )
        return PreparedStreamTraceUpdate(config=config, redactor=redactor)

    def commit_update(self, prepared: PreparedStreamTraceUpdate) -> None:
        """Commit prepared trace settings using assignments only."""
        self.config = prepared.config
        self._redactor = prepared.redactor

    def update(
        self,
        config: StreamTraceConfig,
        *,
        token_values: Iterable[str] | None = None,
    ) -> None:
        """Apply new settings without restarting the gateway."""
        self.commit_update(self.prepare_update(config, token_values=token_values))

    def create_logger(
        self,
        *,
        request_id: str | None,
        request_log_id: str | None,
        model: str,
        source_provider: ProviderType,
        target_provider: ProviderType,
        provider_name: str,
        force: bool = False,
    ) -> StreamTraceLogger | None:
        """Create a trace logger for one stream if current settings match."""
        config = self.config
        if not config.enabled:
            return None

        if not force and not _matches_filter(
            config.filter,
            model=model,
            source_provider=source_provider,
            target_provider=target_provider,
            provider_name=provider_name,
        ):
            return None

        return StreamTraceLogger(
            path=_resolve_trace_path(config.path),
            request_id=request_id,
            request_log_id=request_log_id,
            model=model,
            source_provider=source_provider,
            target_provider=target_provider,
            provider_name=provider_name,
            max_string_chars=config.max_string_chars,
            redactor=self._redactor,
        )


class StreamTraceLogger:
    """Write per-stream conversion diagnostics to an external JSONL file."""

    def __init__(
        self,
        *,
        path: Path,
        request_id: str | None,
        request_log_id: str | None,
        model: str,
        source_provider: ProviderType,
        target_provider: ProviderType,
        provider_name: str,
        max_string_chars: int = DEFAULT_MAX_CHARS,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self.path = path
        self.request_id = request_id
        self.request_log_id = request_log_id
        self.model = model
        self.source_provider = source_provider
        self.target_provider = target_provider
        self.provider_name = provider_name
        self.max_string_chars = max_string_chars
        self._redactor = redactor or SecretRedactor()
        self._disabled = False

    def log(
        self,
        stage: str,
        data: Any,
        *,
        chunk_index: int | None = None,
    ) -> None:
        """Append one trace record to the JSONL file."""
        if self._disabled:
            return

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": self.request_id,
            "request_log_id": self.request_log_id,
            "model": self.model,
            "source_provider": self.source_provider,
            "target_provider": self.target_provider,
            "provider_name": self.provider_name,
            "chunk_index": chunk_index,
            "stage": stage,
            "data": _truncate(self._redactor.redact(data), self.max_string_chars),
        }
        try:
            parent_existed = self.path.parent.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not parent_existed:
                os.chmod(self.path.parent, 0o700)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            self._disabled = True
            logger.warning("Disabling stream trace after write failure: %s", exc)


def _matches_filter(
    filter_value: str,
    *,
    model: str,
    source_provider: ProviderType,
    target_provider: ProviderType,
    provider_name: str,
) -> bool:
    filter_value = filter_value.strip()
    if not filter_value:
        return True

    haystack = " ".join(
        [model, str(source_provider), str(target_provider), provider_name]
    ).lower()
    needles = [part.strip().lower() for part in filter_value.split(",")]
    return any(needle and needle in haystack for needle in needles)


def _resolve_trace_path(path: str | None) -> Path:
    return Path(str(path or "").strip() or DEFAULT_TRACE_PATH).expanduser()


def _truncate(value: Any, max_string_chars: int) -> Any:
    if isinstance(value, str):
        if len(value) <= max_string_chars:
            return value
        remaining = len(value) - max_string_chars
        return f"{value[:max_string_chars]}...[{remaining} more chars]"
    if isinstance(value, list):
        return [_truncate(item, max_string_chars) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _truncate(item, max_string_chars) for key, item in value.items()
        }
    return value
