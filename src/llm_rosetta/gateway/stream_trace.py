"""Optional JSONL diagnostics for gateway streaming conversions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from llm_rosetta.auto_detect import ProviderType

logger = logging.getLogger("llm-rosetta-gateway")

DEFAULT_MAX_CHARS = 20_000
DEFAULT_TRACE_PATH = "~/.config/llm-rosetta-gateway/log.jsonl"


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
        except (TypeError, ValueError):
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


class StreamTraceState:
    """Mutable stream trace settings used by the running gateway."""

    def __init__(self, config: StreamTraceConfig | None = None) -> None:
        self.config = config or StreamTraceConfig()

    def update(self, config: StreamTraceConfig) -> None:
        """Apply new settings without restarting the gateway."""
        self.config = config

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
        if not force and not config.enabled:
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
    ) -> None:
        self.path = path
        self.request_id = request_id
        self.request_log_id = request_log_id
        self.model = model
        self.source_provider = source_provider
        self.target_provider = target_provider
        self.provider_name = provider_name
        self.max_string_chars = max_string_chars
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
            "timestamp": datetime.now(UTC).isoformat(),
            "request_id": self.request_id,
            "request_log_id": self.request_log_id,
            "model": self.model,
            "source_provider": self.source_provider,
            "target_provider": self.target_provider,
            "provider_name": self.provider_name,
            "chunk_index": chunk_index,
            "stage": stage,
            "data": _truncate(data, self.max_string_chars),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
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
