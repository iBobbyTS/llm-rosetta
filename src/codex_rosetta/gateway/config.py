"""Gateway configuration: JSONC loading, env-var substitution, validation."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.observability.redaction import collect_token_values
from codex_rosetta.observability.retention import resolve_request_log_caps
from codex_rosetta.routing import ResolvedRoute

from .providers import build_provider_info
from .stream_trace import StreamTraceConfig
from .tool_profiles import (
    BUILTIN_TOOL_PROFILE,
    normalize_tool_profile_input_overrides,
    normalize_tool_profile_documents,
    resolve_tool_profile,
    resolve_tool_profile_inputs,
    validate_tool_profile_reference,
)
from .model_presets import model_input_modalities, normalize_model_info
from .web_run_capabilities import WEB_RUN_BASIC_SEARCH_CAPABILITY
from .transport import ProviderInfo

logger = logging.getLogger("codex-rosetta-gateway")

# ---------------------------------------------------------------------------
# Config directory search path
# ---------------------------------------------------------------------------

CONFIG_FILENAME = "config.jsonc"
DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/codex-rosetta-gateway")
CONFIG_DIRS_TO_TRY = [DEFAULT_CONFIG_DIR]
CODEX_HOME_ENV = "CODEX_HOME"
DEFAULT_CODEX_HOME = os.path.expanduser("~/.codex")

API_TYPE_TO_PROVIDER_TYPE: dict[str, str] = {
    "responses": "openai_responses",
    "chat": "openai_chat",
    "anthropic": "anthropic",
    "google": "google",
}
API_TYPE_ORDER = tuple(API_TYPE_TO_PROVIDER_TYPE)

CHAT_DEFAULT_TOOL_PROFILE = BUILTIN_TOOL_PROFILE
RESPONSES_PASSTHROUGH_TOOL_PROFILE = "openai-responses-tool-mapping-only"
WEB_RUN_INJECTION_TOOL_PROFILE = "web-run-injection"
RESPONSES_TOOL_MAPPING_PROFILE = "responses-tool-mapping"

MAX_API_KEY_LABEL_LENGTH = 128
REQUEST_BODY_LIMIT_OPTIONS_MB = (64, 128, 256, 512, 1024)
DEFAULT_REQUEST_BODY_LIMIT_MB = 128
UNLIMITED_REQUEST_BODY_LIMIT = "unlimited"
WEB_RUN_SIDECAR_URL_ENV = "CODEX_ROSETTA_WEB_RUN_URL"
WEB_RUN_SIDECAR_TOKEN_ENV = "CODEX_ROSETTA_WEB_RUN_TOKEN"
SELF_HOSTED_WEB_SEARCH_PROVIDERS = frozenset(
    {"self_hosted_google", "self_hosted_bing", "self_hosted_bing_browser"}
)
WEB_SEARCH_PROVIDERS = frozenset({"tavily", *SELF_HOSTED_WEB_SEARCH_PROVIDERS})
CODEX_MEMORY_MODEL_FIELDS = ("extract_model", "consolidation_model")

# Provider vendor/variant is intentionally not persisted. These exact URL
# matches are the runtime source of truth for selecting a bundled shim. Any
# other URL remains an allowed custom endpoint for the selected protocol.
_PRESET_SHIMS_BY_URL: dict[tuple[str, str], str] = {
    ("chat", "https://api.deepseek.com"): "deepseek",
    ("chat", "https://open.bigmodel.cn/api/paas/v4"): "zhipu",
    ("chat", "https://api.moonshot.cn/v1"): "moonshot",
    ("chat", "https://api.moonshot.ai/v1"): "moonshot",
    ("chat", "https://api.minimaxi.com/v1"): "minimax--openai_chat",
    ("anthropic", "https://api.minimaxi.com/anthropic"): "minimax--anthropic",
    ("chat", "https://dashscope.aliyuncs.com/compatible-mode/v1"): "qwen",
    ("chat", "https://api.openai.com/v1"): "openai",
    ("responses", "https://api.openai.com/v1"): "openai_responses",
    ("google", "https://generativelanguage.googleapis.com"): "google",
    ("anthropic", "https://api.anthropic.com"): "anthropic",
    ("chat", "https://openrouter.ai/api/v1"): "openrouter--openai_chat",
    ("anthropic", "https://openrouter.ai/api"): "openrouter--anthropic",
}

_PRESET_PROTOCOL_URLS = frozenset(
    {
        *_PRESET_SHIMS_BY_URL,
        ("anthropic", "https://api.deepseek.com/anthropic"),
        ("anthropic", "https://api.moonshot.cn/anthropic"),
        ("anthropic", "https://api.moonshot.ai/anthropic"),
        ("responses", "https://api.minimaxi.com/v1"),
        ("responses", "https://api.minimax.io/v1"),
        ("chat", "https://api.minimax.io/v1"),
        ("anthropic", "https://api.minimax.io/anthropic"),
        (
            "responses",
            "https://{WorkspaceId}.{RegionId}.maas.aliyuncs.com/compatible-mode/v1",
        ),
        (
            "chat",
            "https://{WorkspaceId}.{RegionId}.maas.aliyuncs.com/compatible-mode/v1",
        ),
        (
            "anthropic",
            "https://{WorkspaceId}.{RegionId}.maas.aliyuncs.com/apps/anthropic",
        ),
        ("chat", "https://opencode.ai/zen/go/v1"),
    }
)


def _provider_shim_for_url(api_type: str, base_url: Any) -> str | None:
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    normalized = base_url.strip().rstrip("/")
    shim_name = _PRESET_SHIMS_BY_URL.get((api_type, normalized))
    if shim_name is None:
        return None
    from codex_rosetta.shims import get_shim

    return shim_name if get_shim(shim_name) is not None else None


def _infer_provider_api_type(base_url: Any) -> str:
    """Choose the first protocol supported by an exact preset URL."""
    if isinstance(base_url, str) and base_url.strip():
        normalized = base_url.strip().rstrip("/")
        for api_type in API_TYPE_ORDER:
            if (api_type, normalized) in _PRESET_PROTOCOL_URLS:
                return api_type
    return API_TYPE_ORDER[0]


def resolve_provider_api_type(
    name: str,
    cfg: dict[str, Any],
    *,
    warn_on_default: bool = False,
) -> str:
    """Return explicit or URL-inferred protocol without changing persisted config."""
    api_type = cfg.get("api_type")
    if api_type:
        value = str(api_type)
        if value not in API_TYPE_TO_PROVIDER_TYPE:
            raise ValueError(f"config: unsupported provider api_type {api_type!r}")
        return value

    if "shim" in cfg or "type" in cfg:
        raise ValueError(
            "config: provider shim/type options are unsupported; "
            "declare api_type and base_url"
        )

    inferred = _infer_provider_api_type(cfg.get("base_url"))
    if warn_on_default:
        logger.warning(
            "config: provider %r missing api_type; defaulting to %r for base_url %r",
            name,
            inferred,
            cfg.get("base_url"),
        )
    return inferred


def normalize_local_mode_settings(server: Any) -> tuple[bool, bool]:
    """Return validated local-mode enablement and first-run confirmation."""
    mapping = server if isinstance(server, dict) else {}
    enabled = mapping.get("local_mode", True)
    confirmed = mapping.get("local_mode_confirmed", False)
    if not isinstance(enabled, bool):
        raise ValueError("config: server.local_mode must be a boolean")
    if not isinstance(confirmed, bool):
        raise ValueError("config: server.local_mode_confirmed must be a boolean")
    return enabled, confirmed


def normalize_codex_settings(value: Any) -> dict[str, Any]:
    """Validate model overrides managed by the Codex local-mode integration."""
    if value is None:
        mapping: dict[str, Any] = {}
    elif isinstance(value, dict):
        mapping = value
    else:
        raise ValueError("config: codex must be an object")

    unsupported = set(mapping) - {"auto_review_model_override", "memories"}
    if unsupported:
        raise ValueError(f"config: codex has unsupported fields: {sorted(unsupported)}")

    normalized: dict[str, Any] = {}
    auto_review_model = mapping.get("auto_review_model_override")
    if auto_review_model is not None:
        if not isinstance(auto_review_model, str) or not auto_review_model.strip():
            raise ValueError(
                "config: codex.auto_review_model_override must be a non-empty string"
            )
        normalized["auto_review_model_override"] = auto_review_model.strip()

    raw_memories = mapping.get("memories")
    if raw_memories is not None and not isinstance(raw_memories, dict):
        raise ValueError("config: codex.memories must be an object")
    memories = raw_memories if isinstance(raw_memories, dict) else {}
    unsupported_memories = set(memories) - set(CODEX_MEMORY_MODEL_FIELDS)
    if unsupported_memories:
        raise ValueError(
            "config: codex.memories has unsupported fields: "
            f"{sorted(unsupported_memories)}"
        )
    normalized_memories: dict[str, str] = {}
    for field in CODEX_MEMORY_MODEL_FIELDS:
        model = memories.get(field)
        if model is None:
            continue
        if not isinstance(model, str) or not model.strip():
            raise ValueError(
                f"config: codex.memories.{field} must be a non-empty string"
            )
        normalized_memories[field] = model.strip()
    if normalized_memories:
        normalized["memories"] = normalized_memories
    return normalized


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


def normalize_web_search(value: Any) -> dict[str, str]:
    """Validate the global Rosetta search-service configuration."""
    if value is None:
        mapping: dict[str, Any] = {}
    elif isinstance(value, dict):
        mapping = value
    else:
        raise ValueError("config: server.web_search must be an object")
    unsupported = set(mapping) - {"provider", "tavily_api_key"}
    if unsupported:
        raise ValueError(
            f"config: server.web_search has unsupported fields: {sorted(unsupported)}"
        )
    provider = mapping.get("provider", "tavily")
    api_key = mapping.get("tavily_api_key", "")
    if not isinstance(provider, str) or provider not in WEB_SEARCH_PROVIDERS:
        raise ValueError(
            "config: server.web_search.provider must be one of "
            f"{sorted(WEB_SEARCH_PROVIDERS)}"
        )
    if not isinstance(api_key, str):
        raise ValueError("config: server.web_search.tavily_api_key must be a string")
    return {"provider": provider, "tavily_api_key": api_key.strip()}


def normalize_web_run_sidecar(
    value: Any,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str | None, str | None, float]:
    """Resolve and validate the optional authenticated ``web-run`` sidecar."""
    if value is None:
        mapping: dict[str, Any] = {}
    elif isinstance(value, dict):
        mapping = value
    else:
        raise ValueError("config: server.web_run must be an object")
    unsupported = set(mapping) - {"base_url", "token", "timeout_seconds"}
    if unsupported:
        raise ValueError(
            f"config: server.web_run has unsupported fields: {sorted(unsupported)}"
        )

    environment = os.environ if environ is None else environ
    environment_url = environment.get(WEB_RUN_SIDECAR_URL_ENV)
    environment_token = environment.get(WEB_RUN_SIDECAR_TOKEN_ENV)
    raw_url = environment_url or mapping.get("base_url", "")
    raw_token = environment_token or mapping.get("token", "")
    if not isinstance(raw_url, str) or not isinstance(raw_token, str):
        raise ValueError("config: server.web_run base_url and token must be strings")
    base_url = raw_url.strip().rstrip("/")
    token = raw_token.strip()
    if bool(base_url) != bool(token):
        raise ValueError(
            "config: server.web_run base_url and token must be configured together"
        )
    if base_url:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "config: server.web_run base_url must be an HTTP(S) URL without "
                "credentials, query, or fragment"
            )
        if parsed.path not in {"", "/"}:
            raise ValueError("config: server.web_run base_url must not contain a path")
    timeout = mapping.get("timeout_seconds", 45.0)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise ValueError("config: server.web_run timeout_seconds must be a number")
    timeout = float(timeout)
    if not 1.0 <= timeout <= 120.0:
        raise ValueError(
            "config: server.web_run timeout_seconds must be between 1 and 120"
        )
    return base_url or None, token or None, timeout


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


def provider_supports_tool_profiles(cfg: Any) -> bool:
    """Return whether a provider is allowed to use model-group Tool Profiles."""
    return isinstance(cfg, dict)


def default_tool_profile_for_provider(cfg: Any) -> str:
    """Return the bundled default Profile for one provider selection."""
    if not isinstance(cfg, dict):
        return CHAT_DEFAULT_TOOL_PROFILE

    api_type = cfg.get("api_type")
    if api_type == "responses":
        shim_name = _provider_shim_for_url(api_type, cfg.get("base_url"))
        if shim_name == "openai_responses":
            return RESPONSES_PASSTHROUGH_TOOL_PROFILE
        if shim_name is None:
            return WEB_RUN_INJECTION_TOOL_PROFILE
        return RESPONSES_TOOL_MAPPING_PROFILE
    if api_type == "chat":
        return CHAT_DEFAULT_TOOL_PROFILE

    # Keep this fallback separate from the Chat branch so future protocols can
    # receive their own default without changing established Chat behavior.
    return CHAT_DEFAULT_TOOL_PROFILE


def resolve_model_tool_profile_names(
    raw_model_groups: Any,
    raw_providers: dict[str, dict[str, str]],
    tool_profiles: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Resolve Profile names for every LLM model group."""
    result: dict[str, str] = {}
    if not isinstance(raw_model_groups, dict):
        return result
    for group_name, group in raw_model_groups.items():
        if not isinstance(group, dict) or group.get("type") != "llm":
            continue
        if not provider_supports_tool_profiles(
            raw_providers.get(group.get("provider"))
        ):
            continue
        profile_name = validate_tool_profile_reference(
            group.get(
                "tool_profile",
                default_tool_profile_for_provider(
                    raw_providers.get(group.get("provider"))
                ),
            ),
            tool_profiles,
            field=f"config: model_groups.{group_name}.tool_profile",
        )
        group_models = group.get("models", {})
        if isinstance(group_models, dict):
            for model_name in group_models:
                result[model_name] = profile_name
    return result


def resolve_provider_config_type_and_shim(
    name: str,
    cfg: dict[str, Any],
    *,
    warn_on_default: bool = False,
) -> tuple[str, str | None]:
    """Resolve a provider config entry to its base API type and optional shim."""
    api_type = resolve_provider_api_type(
        name,
        cfg,
        warn_on_default=warn_on_default,
    )
    provider_type = api_type_to_provider_type(api_type) or api_type
    return provider_type, _provider_shim_for_url(api_type, cfg.get("base_url"))


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


def config_path_for_dir(config_dir: str) -> str:
    """Return the gateway config file path inside *config_dir*."""
    return os.path.join(config_dir, CONFIG_FILENAME)


def resolve_codex_home(explicit_path: str | None = None) -> str:
    """Resolve Codex Home from CLI override, environment, or its default."""
    raw_path = (
        explicit_path
        if explicit_path is not None
        else os.environ.get(CODEX_HOME_ENV, DEFAULT_CODEX_HOME)
    )
    if not raw_path.strip():
        raise ValueError("Codex Home path must not be empty")
    return os.path.abspath(os.path.expanduser(raw_path))


def discover_config(explicit_dir: str | None = None) -> str | None:
    """Find the first existing config file.

    If *explicit_dir* is given, return its ``config.jsonc`` path
    unconditionally (the caller handles a missing file). Otherwise search
    ``CONFIG_DIRS_TO_TRY`` in order and return the first hit, or ``None``.
    """
    if explicit_dir is not None:
        return config_path_for_dir(explicit_dir)
    for config_dir in CONFIG_DIRS_TO_TRY:
        path = config_path_for_dir(config_dir)
        if os.path.isfile(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Config class
# ---------------------------------------------------------------------------


class GatewayConfig:
    """Parsed and validated gateway configuration."""

    @classmethod
    def from_raw_with_env(cls, raw: dict[str, Any]) -> GatewayConfig:
        """Resolve environment placeholders in a raw candidate and validate it."""
        resolved = _substitute_env_vars(raw)
        return cls(resolved)

    def __init__(self, raw: dict[str, Any]) -> None:
        self.token_values = collect_token_values(raw)
        self.codex = normalize_codex_settings(raw.get("codex"))
        all_providers: dict[str, dict[str, str]] = raw.get("providers", {})

        # Filter out disabled providers (enabled defaults to True)
        self._raw_providers: dict[str, dict[str, str]] = {
            name: dict(cfg)
            for name, cfg in all_providers.items()
            if cfg.get("enabled", True) is not False
        }

        self.provider_types, self.provider_shim_names = self._resolve_provider_types(
            self._raw_providers
        )
        # Top-level ``models`` is intentionally ignored. Model groups are the
        # only persisted routing definition; this flat mapping is runtime-only.
        self._expanded_raw_models = self._expand_model_groups(
            raw.get("model_groups", {})
        )
        self.models, self.model_input_modalities, self.model_upstream_names = (
            self._parse_models(self._expanded_raw_models, self._raw_providers)
        )
        self.tool_profile_documents = normalize_tool_profile_documents(
            raw.get("tool_profiles")
        )
        self.tool_profile_input_overrides = normalize_tool_profile_input_overrides(
            raw.get("tool_profile_input_overrides")
        )
        self.tool_profiles = {
            name: profile["tools"]
            for name, profile in self.tool_profile_documents.items()
        }
        self.model_tool_profile_names = resolve_model_tool_profile_names(
            raw.get("model_groups", {}), self._raw_providers, self.tool_profiles
        )

        _server = raw.get("server", {})
        self.host: str = _server.get("host", "127.0.0.1")
        self.port: int = _server.get("port", 8765)
        self.proxy: str | None = _server.get("proxy")
        self.socket: str | None = _server.get("socket")
        self.local_mode, self.local_mode_confirmed = normalize_local_mode_settings(
            _server
        )
        self.web_search = normalize_web_search(_server.get("web_search"))
        if self.web_search["tavily_api_key"]:
            self.token_values.add(self.web_search["tavily_api_key"])
        (
            self.web_run_sidecar_url,
            self.web_run_sidecar_token,
            self.web_run_sidecar_timeout,
        ) = normalize_web_run_sidecar(_server.get("web_run"))
        if self.web_run_sidecar_token:
            self.token_values.add(self.web_run_sidecar_token)
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
        if "api_key" in _server:
            raise ValueError(
                "config: server.api_key is unsupported; use server.api_keys"
            )
        # Multi-key auth is the only supported persisted shape.
        self.api_keys: list[dict[str, str]] = _server.get("api_keys", [])
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
                "config: no routable models — model groups may reference disabled providers"
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
        """Resolve each provider's explicit protocol and URL-derived shim.

        Returns:
            Tuple of (provider_types, provider_shim_names).
        """
        provider_types: dict[str, str] = {}
        provider_shim_names: dict[str, str | None] = {}
        for name, cfg in raw_providers.items():
            api_type = resolve_provider_api_type(name, cfg, warn_on_default=True)
            cfg["api_type"] = api_type
            provider_type, shim_name = resolve_provider_config_type_and_shim(name, cfg)
            provider_types[name] = provider_type
            provider_shim_names[name] = shim_name
        return provider_types, provider_shim_names

    @classmethod
    def _expand_group_model(
        cls,
        *,
        group_name: str,
        provider_name: str,
        model_name: str,
        model_value: Any,
    ) -> dict[str, Any]:
        """Normalize one persisted group member into a runtime route entry."""
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

        model_info = entry.pop("model_info", None)
        if model_info is not None:
            model_info = normalize_model_info(
                model_info,
                field=(
                    f"config: model '{model_name}' in model group "
                    f"'{group_name}'.model_info"
                ),
            )
        unsupported = set(entry) - {"provider", "upstream_model"}
        if unsupported:
            raise ValueError(
                f"config: model '{model_name}' in model group "
                f"'{group_name}' has unsupported fields: {sorted(unsupported)}"
            )
        return entry

    @classmethod
    def _expand_model_groups(
        cls,
        raw_model_groups: dict[str, Any],
    ) -> dict[str, Any]:
        """Expand the only supported routing definition into a runtime table."""
        expanded: dict[str, Any] = {}
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
            group_type = group_value.get("type")
            if group_type != "llm":
                raise ValueError(
                    f"config: model group '{group_name}' type must be 'llm'"
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
                expanded[model_name] = cls._expand_group_model(
                    group_name=group_name,
                    provider_name=provider_name,
                    model_name=model_name,
                    model_value=model_value,
                )
        return expanded

    @classmethod
    def _parse_models(
        cls,
        raw_models: dict[str, Any],
        raw_providers: dict[str, dict[str, str]],
    ) -> tuple[dict[str, ProviderType], dict[str, list[str] | None], dict[str, str]]:
        """Parse model routing entries from config.

        Entries have already been normalized from model groups.

        Models referencing disabled/missing providers are silently skipped.

        Returns:
            Tuple of (models, model input modalities, model upstream names).
        """
        models: dict[str, ProviderType] = {}
        input_modalities: dict[str, list[str] | None] = {}
        model_upstream_names: dict[str, str] = {}
        for name, value in raw_models.items():
            if not isinstance(value, dict):
                raise ValueError(f"config: invalid model entry for '{name}'")
            provider_name = value["provider"]

            if provider_name not in raw_providers:
                continue

            models[name] = provider_name
            upstream = value.get("upstream_model")
            input_modalities[name] = model_input_modalities(
                name,
                upstream,
            )
            if upstream:
                model_upstream_names[name] = upstream
        return models, input_modalities, model_upstream_names

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
        input-modality detection and reasoning overrides into a single typed
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
        input_modalities = self.model_input_modalities.get(model)
        tool_profile_name = self.model_tool_profile_names.get(model)

        route = ResolvedRoute(
            source_provider=source_provider,
            target_provider=cast(ProviderType, provider_type),
            provider_name=provider_name,
            shim_name=shim_name,
            upstream_model=upstream_model,
            input_modalities=input_modalities,
            tool_profile_name=tool_profile_name,
            tool_profile=(
                resolve_tool_profile(tool_profile_name, self.tool_profiles)
                if tool_profile_name is not None
                else {}
            ),
            tool_profile_inputs=(
                resolve_tool_profile_inputs(
                    tool_profile_name,
                    self.tool_profile_documents,
                    self.tool_profile_input_overrides,
                )
                if tool_profile_name is not None
                else {}
            ),
            tool_runtime_capabilities=(
                frozenset({WEB_RUN_BASIC_SEARCH_CAPABILITY})
                if self.web_search["provider"] == "tavily"
                and self.web_search["tavily_api_key"]
                else frozenset()
            ),
        )
        return route, self.providers[provider_name]
