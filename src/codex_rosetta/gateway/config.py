"""Gateway configuration: JSONC loading, env-var substitution, validation."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import sys
import tempfile
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.observability.redaction import collect_token_values
from codex_rosetta.observability.retention import resolve_request_log_caps
from codex_rosetta.reasoning_mapping import normalize_reasoning_mapping
from codex_rosetta.routing import ResolvedRoute

from .providers import build_provider_info
from .stream_trace import StreamTraceConfig
from .tool_adaptation import validate_tool_call_cache_ttl_hours
from .transport import ProviderInfo

logger = logging.getLogger("codex-rosetta-gateway")

# ---------------------------------------------------------------------------
# Config file search paths (checked in order)
# ---------------------------------------------------------------------------

PATHS_TO_TRY = [
    "./config.jsonc",
    os.path.expanduser("~/.config/codex-rosetta-gateway/config.jsonc"),
    os.path.expanduser("~/.codex-rosetta-gateway/config.jsonc"),
]

API_TYPE_TO_PROVIDER_TYPE: dict[str, str] = {
    "responses": "openai_responses",
    "chat": "openai_chat",
    "anthropic": "anthropic",
    "google": "google",
}

PROVIDER_API_TYPE_SHIMS: dict[tuple[str, str], str] = {
    ("anthropic", "anthropic"): "anthropic",
    ("deepseek", "chat"): "deepseek",
    ("google", "google"): "google",
    ("minimax_china", "anthropic"): "minimax--anthropic",
    ("minimax_china", "chat"): "minimax--openai_chat",
    ("minimax_international", "anthropic"): "minimax--anthropic",
    ("minimax_international", "chat"): "minimax--openai_chat",
    ("moonshot_china", "chat"): "moonshot",
    ("moonshot_international", "chat"): "moonshot",
    ("openai", "chat"): "openai",
    ("openai", "responses"): "openai_responses",
    ("openrouter", "anthropic"): "openrouter--anthropic",
    ("openrouter", "chat"): "openrouter--openai_chat",
    ("qwen", "chat"): "qwen",
    ("zhipu", "chat"): "zhipu",
}

MAX_API_KEY_LABEL_LENGTH = 128
REQUEST_BODY_LIMIT_OPTIONS_MB = (64, 128, 256, 512, 1024)
DEFAULT_REQUEST_BODY_LIMIT_MB = 128
UNLIMITED_REQUEST_BODY_LIMIT = "unlimited"


def normalize_request_body_limit_mb(value: Any) -> int | None:
    """Validate the configured inbound request-body limit.

    ``None`` is the normalized runtime representation of the explicit
    ``"unlimited"`` setting. Numeric limits use MiB-sized units even though the
    user-facing configuration keeps the shorter ``_mb`` spelling.
    """
    if value == UNLIMITED_REQUEST_BODY_LIMIT:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            "config: server.request_body_limit_mb must be one of "
            "64, 128, 256, 512, 1024, or 'unlimited'"
        )
    if value not in REQUEST_BODY_LIMIT_OPTIONS_MB:
        raise ValueError(
            "config: server.request_body_limit_mb must be one of "
            "64, 128, 256, 512, 1024, or 'unlimited'"
        )
    return value


def validate_api_key_label(value: Any, *, field: str = "label") -> str:
    """Validate a gateway access-key display label and return it unchanged."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if len(value) > MAX_API_KEY_LABEL_LENGTH:
        raise ValueError(
            f"{field} must be at most {MAX_API_KEY_LABEL_LENGTH} characters"
        )
    return value


def normalize_admin_cors_origins(value: Any) -> list[str]:
    """Validate and canonicalize the Admin CORS origin allowlist."""
    if not isinstance(value, list):
        raise ValueError("config: server.admin_cors_origins must be a list")

    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(
                f"config: server.admin_cors_origins[{index}] must be a string"
            )
        raw = item.strip()
        if not raw or any(char.isspace() for char in raw):
            raise ValueError(
                f"config: server.admin_cors_origins[{index}] must be an HTTP(S) origin"
            )
        parsed = urlsplit(raw)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                f"config: server.admin_cors_origins[{index}] must be an HTTP(S) origin "
                "without credentials, path, query, or fragment"
            )
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(
                f"config: server.admin_cors_origins[{index}] has an invalid port"
            ) from exc

        scheme = parsed.scheme.lower()
        hostname = parsed.hostname.lower()
        host = f"[{hostname}]" if ":" in hostname else hostname
        if port is not None and not (
            (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        ):
            host = f"{host}:{port}"
        origin = f"{scheme}://{host}"
        if origin not in seen:
            seen.add(origin)
            normalized.append(origin)
    return normalized


def api_type_to_provider_type(api_type: Any) -> str | None:
    """Return the base gateway provider type for an admin protocol value."""
    if not api_type:
        return None
    return API_TYPE_TO_PROVIDER_TYPE.get(str(api_type))


def derive_provider_shim_name(provider: Any, api_type: Any) -> str | None:
    """Return the registered shim for a provider/protocol pair, when available."""
    if not provider or not api_type:
        return None
    shim_name = PROVIDER_API_TYPE_SHIMS.get((str(provider), str(api_type)))
    if not shim_name:
        return None

    from codex_rosetta.shims import get_shim

    return shim_name if get_shim(shim_name) is not None else None


def resolve_provider_config_type_and_shim(
    name: str, cfg: dict[str, Any]
) -> tuple[str, str | None]:
    """Resolve a provider config entry to its base API type and optional shim."""
    api_type = cfg.get("api_type")
    if api_type:
        provider_type = api_type_to_provider_type(api_type) or str(api_type)
        return provider_type, derive_provider_shim_name(cfg.get("provider"), api_type)

    from codex_rosetta.shims import resolve_base

    if "shim" in cfg:
        return resolve_base(cfg["shim"]), cfg["shim"]
    if "type" in cfg:
        return resolve_base(cfg["type"]), cfg["type"]
    return name, name


# ---------------------------------------------------------------------------
# JSONC loader
# ---------------------------------------------------------------------------

_JSONC_COMMENT_RE = re.compile(
    r'("(?:[^"\\]|\\.)*")|//[^\n]*|/\*[\s\S]*?\*/', re.MULTILINE
)
_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


class ConfigConflictError(RuntimeError):
    """Raised when a config changed after it was loaded for editing."""


class ConfigDocument(dict[str, Any]):
    """Mutable config mapping carrying its source-file content digest."""

    def __init__(self, value: dict[str, Any], *, source_digest: str) -> None:
        super().__init__(value)
        self.source_digest = source_digest


def _strip_jsonc_comments(text: str) -> str:
    """Remove // and /* */ comments from JSONC, preserving strings."""

    def _replace(m: re.Match) -> str:
        if m.group(1) is not None:
            return m.group(1)  # quoted string — keep it
        return ""

    return _JSONC_COMMENT_RE.sub(_replace, text)


def _substitute_env_vars(value: Any) -> Any:
    """Resolve ${ENV_VAR} placeholders inside parsed JSON string values."""

    if isinstance(value, dict):
        return {key: _substitute_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    if not isinstance(value, str):
        return value

    def _replace(m: re.Match) -> str:
        var_name = m.group(1)
        value = os.environ.get(var_name)
        if value is None:
            logger.warning("Environment variable %s is not set", var_name)
            return m.group(0)  # leave placeholder intact
        return value

    return _ENV_VAR_RE.sub(_replace, value)


def load_config(path: str) -> dict[str, Any]:
    """Load and parse a JSONC config file with env-var substitution."""
    with open(path) as f:
        raw = f.read()
    stripped = _strip_jsonc_comments(raw)
    parsed = json.loads(stripped)
    return _substitute_env_vars(parsed)


def _content_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ensure_private_directory(path: str) -> None:
    """Create missing directory components with owner-only permissions."""
    directory = os.path.abspath(path)
    missing: list[str] = []
    current = directory
    while not os.path.exists(current):
        missing.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    os.makedirs(directory, mode=0o700, exist_ok=True)
    for created in missing:
        os.chmod(created, 0o700)


def _atomic_write_bytes(path: str, content: bytes) -> None:
    """Atomically replace *path* with fsynced owner-only bytes."""
    parent = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(path: str) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def write_config(
    path: str,
    data: dict[str, Any],
    *,
    activate: Callable[[], None] | None = None,
) -> None:
    """Crash-safely write config using a lock, digest CAS, and backup.

    A :class:`ConfigDocument` loaded by :func:`load_config_raw` is rejected if
    the file changed before this write. Comments are not preserved. When
    *activate* is provided it runs while the write lock is held; a callback
    failure restores the exact previous file bytes before the exception is
    re-raised.
    """
    import fcntl

    parent = os.path.dirname(os.path.abspath(path)) or "."
    _ensure_private_directory(parent)
    serialized = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    lock_path = f"{path}.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(lock_fd, 0o600)
        with os.fdopen(lock_fd, "r+") as lock_file:
            lock_fd = -1
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            current = b""
            if os.path.exists(path):
                with open(path, "rb") as existing:
                    current = existing.read()
            expected_digest = getattr(data, "source_digest", None)
            current_digest = _content_digest(current)
            if expected_digest is not None and expected_digest != current_digest:
                raise ConfigConflictError(
                    "config changed on disk after it was loaded; reload and retry"
                )

            if current:
                _atomic_write_bytes(f"{path}.bak", current)
            _atomic_write_bytes(path, serialized)
            try:
                _fsync_directory(parent)
                if activate is not None:
                    activate()
            except Exception:
                if current:
                    _atomic_write_bytes(path, current)
                else:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass
                _fsync_directory(parent)
                raise
            if isinstance(data, ConfigDocument):
                data.source_digest = _content_digest(serialized)
    finally:
        if lock_fd >= 0:
            os.close(lock_fd)


def load_config_raw(path: str) -> ConfigDocument:
    """Load and parse a JSONC config file *without* env-var substitution.

    Useful for reading config that will be written back (e.g. ``add`` CLI).
    """
    with open(path, "rb") as f:
        raw_bytes = f.read()
    raw = raw_bytes.decode("utf-8")
    stripped = _strip_jsonc_comments(raw)
    return ConfigDocument(
        json.loads(stripped), source_digest=_content_digest(raw_bytes)
    )


def discover_config(explicit_path: str | None = None) -> str | None:
    """Find the first existing config file.

    If *explicit_path* is given, return it unconditionally (caller is
    responsible for handling missing files).  Otherwise search
    ``PATHS_TO_TRY`` in order and return the first hit, or ``None``.
    """
    if explicit_path is not None:
        return explicit_path
    for path in PATHS_TO_TRY:
        if os.path.isfile(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Config class
# ---------------------------------------------------------------------------


class GatewayConfig:
    """Parsed and validated gateway configuration."""

    # Default capabilities when not specified in config.
    DEFAULT_CAPABILITIES: list[str] = ["text"]

    @classmethod
    def from_raw_with_env(cls, raw: dict[str, Any]) -> GatewayConfig:
        """Resolve environment placeholders in a raw candidate and validate it."""
        resolved = _substitute_env_vars(raw)
        return cls(resolved)

    def __init__(self, raw: dict[str, Any]) -> None:
        self.token_values = collect_token_values(raw)
        all_providers: dict[str, dict[str, str]] = raw.get("providers", {})

        # Filter out disabled providers (enabled defaults to True)
        self._raw_providers: dict[str, dict[str, str]] = {
            name: cfg
            for name, cfg in all_providers.items()
            if cfg.get("enabled", True) is not False
        }

        self.provider_types, self.provider_shim_names = self._resolve_provider_types(
            self._raw_providers
        )

        self._expanded_raw_models = self._expand_model_groups(
            raw.get("models", {}), raw.get("model_groups", {})
        )
        self.models, self.model_capabilities, self.model_upstream_names = (
            self._parse_models(self._expanded_raw_models, self._raw_providers)
        )

        # Per-model reasoning mappings from config.jsonc (admin UI edits).
        # Keyed by gateway model name (same as self.models keys).
        self.model_reasoning_mappings: dict[str, str] = {}
        self.model_tool_adaptations: dict[str, dict[str, Any]] = {}
        for model_name, value in self._expanded_raw_models.items():
            if isinstance(value, dict) and "reasoning_mapping" in value:
                self.model_reasoning_mappings[model_name] = normalize_reasoning_mapping(
                    value.get("reasoning_mapping")
                )
            if isinstance(value, dict) and value.get("tool_adaptation"):
                tool_adaptation = value["tool_adaptation"]
                if not isinstance(tool_adaptation, dict):
                    raise ValueError(
                        f"config: model '{model_name}' tool_adaptation must be an object"
                    )
                normalized_tool_adaptation = dict(tool_adaptation)
                if "tool_call_cache_ttl_hours" in normalized_tool_adaptation:
                    normalized_tool_adaptation["tool_call_cache_ttl_hours"] = (
                        validate_tool_call_cache_ttl_hours(
                            normalized_tool_adaptation["tool_call_cache_ttl_hours"],
                            field=(
                                f"config: model '{model_name}' "
                                "tool_adaptation.tool_call_cache_ttl_hours"
                            ),
                        )
                    )
                self.model_tool_adaptations[model_name] = normalized_tool_adaptation

        _server = raw.get("server", {})
        self.host: str = _server.get("host", "127.0.0.1")
        self.port: int = _server.get("port", 8765)
        self.proxy: str | None = _server.get("proxy")
        self.socket: str | None = _server.get("socket")
        self.credential_visible: bool = _server.get("credential_visible", False)
        self.request_body_limit_mb = normalize_request_body_limit_mb(
            _server.get("request_body_limit_mb", DEFAULT_REQUEST_BODY_LIMIT_MB)
        )
        self.request_body_limit_config_value: int | str = (
            UNLIMITED_REQUEST_BODY_LIMIT
            if self.request_body_limit_mb is None
            else self.request_body_limit_mb
        )
        self.request_body_limit_bytes = (
            sys.maxsize
            if self.request_body_limit_mb is None
            else self.request_body_limit_mb * 1024 * 1024
        )
        self.admin_password: str | None = _server.get("admin_password")
        if isinstance(self.admin_password, str) and _ENV_VAR_RE.search(
            self.admin_password
        ):
            raise ValueError(
                "config: admin_password contains an unresolved ${...} placeholder. "
                "Set the environment variable or use a literal password."
            )

        # CORS allow-list for /admin/* endpoints.
        # Default [] means same-origin only (no Access-Control-Allow-Origin header).
        # To permit a specific trusted origin set e.g. in your config (JSONC):
        #   "server": {
        #     "admin_cors_origins": ["https://my-admin.example.com"]
        #   }
        self.admin_cors_origins = normalize_admin_cors_origins(
            _server.get("admin_cors_origins", [])
        )

        # Resolve request-log retention during config construction so startup
        # and Admin hot reload use the same strict validation boundary.
        self.request_log: Any = _server.get("request_log", {})
        (
            self.request_log_success_max,
            self.request_log_error_max,
        ) = resolve_request_log_caps(self.request_log)
        self.stream_trace: StreamTraceConfig = StreamTraceConfig.from_mapping(
            _server.get("stream_trace", {})
        )
        web_search = _server.get("web_search", {}) or {}
        self.web_search: dict[str, Any] = (
            dict(web_search) if isinstance(web_search, dict) else {}
        )

        # Multi-key auth: server.api_keys takes precedence over server.api_key
        self.api_keys: list[dict[str, str]] = _server.get("api_keys", [])
        if not self.api_keys and _server.get("api_key"):
            # Backward compat: single api_key → synthetic entry
            self.api_keys = [
                {
                    "id": "default",
                    "key": _server["api_key"],
                    "label": "default",
                    "created": "",
                }
            ]
        # Sensitive body logging option (config + env-var override)
        _debug = raw.get("debug", {})
        self.log_bodies: bool = _debug.get("log_bodies", False) or os.environ.get(
            "CODEX_ROSETTA_LOG_BODIES", ""
        ).lower() in ("1", "true", "yes")

        self._validate()

        # Raw-key lookup maps remain in memory only.  Persistent/request state
        # uses the stable configured ID, never the display label or raw key.
        self.api_key_set: frozenset[str] = frozenset(
            entry["key"] for entry in self.api_keys
        )
        self.api_key_labels: dict[str, str] = {
            entry["key"]: entry.get("label", "") for entry in self.api_keys
        }
        self.api_key_principals: dict[str, str] = {
            entry["key"]: entry["id"] for entry in self.api_keys
        }

        # Build ProviderInfo objects (with key rotation support)
        self.providers: dict[str, ProviderInfo] = {
            name: build_provider_info(
                self.provider_types[name], cfg, global_proxy=self.proxy
            )
            for name, cfg in self._raw_providers.items()
        }

    def _validate(self) -> None:
        if not isinstance(self.admin_password, str) or not self.admin_password.strip():
            raise ValueError("config: server.admin_password must be a non-empty string")
        if not isinstance(self.api_keys, list) or not self.api_keys:
            raise ValueError("config: at least one server.api_keys entry is required")

        seen_ids: set[str] = set()
        seen_keys: set[str] = set()
        for index, entry in enumerate(self.api_keys):
            if not isinstance(entry, dict):
                raise ValueError(f"config: server.api_keys[{index}] must be an object")
            principal = entry.get("id")
            key = entry.get("key")
            if not isinstance(principal, str) or not principal.strip():
                raise ValueError(
                    f"config: server.api_keys[{index}].id must be a non-empty string"
                )
            if principal == "__admin_internal__":
                raise ValueError(
                    "config: server.api_keys[].id uses a reserved internal principal"
                )
            if principal in seen_ids:
                raise ValueError(
                    f"config: duplicate server.api_keys[].id '{principal}'"
                )
            if not isinstance(key, str) or not key.strip():
                raise ValueError(
                    f"config: server.api_keys[{index}].key must be a non-empty string"
                )
            if _ENV_VAR_RE.search(key):
                raise ValueError(
                    f"config: server.api_keys[{index}].key contains an unresolved "
                    "${...} placeholder. Set the environment variable."
                )
            if key in seen_keys:
                raise ValueError("config: duplicate server.api_keys[].key")
            validate_api_key_label(
                entry.get("label", ""),
                field=f"config: server.api_keys[{index}].label",
            )
            seen_ids.add(principal)
            seen_keys.add(key)

        if not self._raw_providers:
            logger.warning(
                "config: no enabled providers — all providers may be disabled"
            )
            return
        if not self.models:
            logger.warning(
                "config: no routable models — models may reference disabled providers"
            )
            return
        for model, provider in self.models.items():
            if provider not in self._raw_providers:
                raise ValueError(
                    f"config: model '{model}' references unknown provider '{provider}'"
                )

    @staticmethod
    def _resolve_provider_types(
        raw_providers: dict[str, dict[str, str]],
    ) -> tuple[dict[str, str], dict[str, str | None]]:
        """Resolve each provider's API standard type via admin protocol or shim.

        Resolution order per provider:
          1. ``api_type`` field → resolve to a base protocol and derive shim
          2. ``shim`` field → resolve via shim registry
          3. ``type`` field → resolve via shim registry
          4. provider name itself (backward-compatible fallback)

        Returns:
            Tuple of (provider_types, provider_shim_names).
        """
        provider_types: dict[str, str] = {}
        provider_shim_names: dict[str, str | None] = {}
        for name, cfg in raw_providers.items():
            provider_type, shim_name = resolve_provider_config_type_and_shim(name, cfg)
            provider_types[name] = provider_type
            provider_shim_names[name] = shim_name
        return provider_types, provider_shim_names

    @classmethod
    def _expand_model_groups(
        cls,
        raw_models: dict[str, Any],
        raw_model_groups: dict[str, Any],
    ) -> dict[str, Any]:
        """Expand admin model groups into the flat routing table shape."""
        expanded = dict(raw_models) if isinstance(raw_models, dict) else {}
        if not raw_model_groups:
            return expanded
        if not isinstance(raw_model_groups, dict):
            raise ValueError("config: 'model_groups' must be an object")

        for group_name, group_value in raw_model_groups.items():
            if not isinstance(group_value, dict):
                raise ValueError(
                    f"config: invalid model group entry for '{group_name}'"
                )
            provider_name = group_value.get("provider")
            if not provider_name:
                raise ValueError(
                    f"config: model group '{group_name}' requires a provider"
                )
            group_models = group_value.get("models", {})
            if not isinstance(group_models, dict):
                raise ValueError(
                    f"config: model group '{group_name}' models must be an object"
                )

            for model_name, model_value in group_models.items():
                if model_name in expanded:
                    raise ValueError(
                        f"config: model '{model_name}' is defined more than once"
                    )
                if isinstance(model_value, str):
                    entry: dict[str, Any] = {"provider": provider_name}
                    if model_value:
                        entry["upstream_model"] = model_value
                elif isinstance(model_value, dict):
                    entry = dict(model_value)
                    entry["provider"] = provider_name
                else:
                    raise ValueError(
                        f"config: invalid model entry for '{model_name}' "
                        f"in model group '{group_name}'"
                    )
                expanded[model_name] = entry
        return expanded

    @classmethod
    def _parse_models(
        cls,
        raw_models: dict[str, Any],
        raw_providers: dict[str, dict[str, str]],
    ) -> tuple[dict[str, ProviderType], dict[str, list[str]], dict[str, str]]:
        """Parse model routing entries from config.

        Supports both string and dict formats:
          - ``"model": "provider"`` (legacy)
          - ``"model": {"provider": "p", "capabilities": [...]}``
          - ``"model": {"provider": "p", "upstream_model": "actual_name"}``

        Models referencing disabled/missing providers are silently skipped.

        Returns:
            Tuple of (models, model_capabilities, model_upstream_names).
        """
        models: dict[str, ProviderType] = {}
        model_capabilities: dict[str, list[str]] = {}
        model_upstream_names: dict[str, str] = {}
        for name, value in raw_models.items():
            if isinstance(value, str):
                provider_name = value
            elif isinstance(value, dict):
                provider_name = value["provider"]
            else:
                raise ValueError(f"config: invalid model entry for '{name}'")

            if provider_name not in raw_providers:
                continue

            models[name] = provider_name
            if isinstance(value, str):
                model_capabilities[name] = list(cls.DEFAULT_CAPABILITIES)
            else:
                model_capabilities[name] = value.get(
                    "capabilities", list(cls.DEFAULT_CAPABILITIES)
                )
                upstream = value.get("upstream_model")
                if upstream:
                    model_upstream_names[name] = upstream
        return models, model_capabilities, model_upstream_names

    @property
    def api_key(self) -> str | None:
        """First configured key (for backward-compat middleware init)."""
        return self.api_keys[0]["key"] if self.api_keys else None

    def resolve(
        self,
        source_provider: ProviderType,
        model: str,
    ) -> tuple[ResolvedRoute, ProviderInfo]:
        """Resolve *model* to a :class:`ResolvedRoute` and :class:`ProviderInfo`.

        Consolidates model lookup, provider type resolution, shim binding,
        capability detection, and reasoning overrides into a single typed
        result.

        Args:
            source_provider: API standard of the incoming request.
            model: Model name as specified by the client.

        Returns:
            ``(route, provider_info)`` — the route contains all
            pipeline-relevant fields; ``provider_info`` is the
            transport-level connection config.

        Raises:
            KeyError: If the model is not in the routing table.
        """
        from typing import cast

        provider_name = self.models[model]
        provider_type = self.provider_types[provider_name]
        shim_name = self.provider_shim_names.get(provider_name)
        upstream_model = self.model_upstream_names.get(model)
        caps = self.model_capabilities.get(model, list(self.DEFAULT_CAPABILITIES))
        reasoning_mapping = self.model_reasoning_mappings.get(model)
        tool_adaptation = self.model_tool_adaptations.get(model)

        route = ResolvedRoute(
            source_provider=source_provider,
            target_provider=cast(ProviderType, provider_type),
            provider_name=provider_name,
            shim_name=shim_name,
            upstream_model=upstream_model,
            model_capabilities=caps,
            reasoning_mapping=reasoning_mapping,
            tool_adaptation=tool_adaptation,
        )
        return route, self.providers[provider_name]
