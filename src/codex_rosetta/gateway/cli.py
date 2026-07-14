"""CLI entry point and subcommands for codex-rosetta gateway."""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from typing import Any

import asyncio

from codex_rosetta import __version__

from .banner import print_banner
from .config import (
    CODEX_HOME_ENV,
    CONFIG_DIRS_TO_TRY,
    DEFAULT_CONFIG_DIR,
    DEFAULT_REQUEST_BODY_LIMIT_MB,
    GatewayConfig,
    config_path_for_dir,
    default_tool_profile_for_provider,
    discover_config,
    load_config,
    load_config_raw,
    provider_supports_tool_profiles,
    resolve_codex_home,
    write_config,
)
from .logging import get_logger, setup_logging
from .local_mode import (
    CodexLocalModeTransaction,
    codex_api_key_value,
    config_toml_has_model_catalog,
    ensure_codex_api_key,
)
from .providers import (
    get_default_api_key_env,
    get_default_base_url,
    known_provider_types,
)

logger = get_logger()

# ---------------------------------------------------------------------------
# Editor helper
# ---------------------------------------------------------------------------


def _open_in_editor(config_dir: str | None = None) -> None:
    """Open a config file in the user's preferred editor."""
    config_dirs = [config_dir] if config_dir else list(CONFIG_DIRS_TO_TRY)
    paths = [config_path_for_dir(directory) for directory in config_dirs]

    editors: list[str] = []
    env_editor = os.getenv("EDITOR")
    if env_editor:
        editors.append(env_editor)
    editors += ["notepad"] if os.name == "nt" else ["nano", "vi", "vim"]

    for path in paths:
        if path and os.path.exists(path):
            for editor in editors:
                try:
                    subprocess.run([editor, path], check=True)
                    return
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    print(
                        f"Error: failed to open {editor} for {path}: {exc}",
                        file=sys.stderr,
                    )
                    sys.exit(1)

    print("Error: no config file found to edit. Searched:", file=sys.stderr)
    for p in paths:
        print(f"  {p}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config file helpers
# ---------------------------------------------------------------------------


def _secure_server_template() -> dict[str, Any]:
    """Return secure, immediately usable server settings for a new config."""
    return {
        "host": "127.0.0.1",
        "port": 8765,
        "local_mode": True,
        "local_mode_confirmed": False,
        "request_body_limit_mb": DEFAULT_REQUEST_BODY_LIMIT_MB,
        "admin_password": secrets.token_urlsafe(32),
        "credential_visible": False,
        "api_keys": [
            {
                "id": "default",
                "label": "Default client",
                "key": f"rsk-{secrets.token_hex(24)}",
            }
        ],
    }


def _empty_config_template() -> dict[str, Any]:
    """Return a new secure config scaffold."""
    return {
        "providers": {},
        "tool_profiles": {},
        "model_groups": {},
        "server": _secure_server_template(),
    }


def _load_or_create_config(path: str) -> tuple[dict[str, Any], str]:
    """Load existing config (raw, no env substitution) or create a scaffold."""
    if os.path.isfile(path):
        return load_config_raw(path), path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = _empty_config_template()
    return data, path


def _config_path_for_write(config_dir: str | None) -> str:
    """Resolve the config file path used by mutating subcommands."""
    return discover_config(config_dir) or config_path_for_dir(DEFAULT_CONFIG_DIR)


def _write_jsonc(path: str, data: dict[str, Any]) -> None:
    write_config(path, data)


def _create_initial_config(config_path: str) -> None:
    """Create the standard secure gateway configuration at *config_path*."""
    template = {
        "providers": {
            "openai_chat": {
                "api_key": "${OPENAI_API_KEY}",
                "base_url": "https://api.openai.com/v1",
            },
            "anthropic": {
                "api_key": "${ANTHROPIC_API_KEY}",
                "base_url": "https://api.anthropic.com",
            },
            "google": {
                "api_key": "${GOOGLE_API_KEY}",
                "base_url": "https://generativelanguage.googleapis.com",
            },
        },
        "model_groups": {
            "OpenAI": {
                "provider": "openai_chat",
                "type": "llm",
                "tool_profile": "builtin",
                "models": {"gpt-4o": {}},
            },
            "Anthropic": {
                "provider": "anthropic",
                "type": "llm",
                "tool_profile": "builtin",
                "models": {"claude-sonnet-4-20250514": {}},
            },
            "Google": {
                "provider": "google",
                "type": "llm",
                "tool_profile": "builtin",
                "models": {"gemini-2.0-flash": {}},
            },
        },
        "tool_profiles": {},
        "server": _secure_server_template(),
    }

    _write_jsonc(config_path, template)
    print(f"Created config at {config_path}")
    print(
        "Generated a mandatory Admin password and gateway access key under "
        "server. Store them securely."
    )


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> None:
    """Create a template config.jsonc at the XDG default location."""
    config_path = config_path_for_dir(args.config or DEFAULT_CONFIG_DIR)
    if os.path.isfile(config_path):
        print(f"Config already exists at {config_path}", file=sys.stderr)
        print("Use --edit / -e to modify it, or remove it first.", file=sys.stderr)
        sys.exit(1)

    _create_initial_config(config_path)
    print("Edit provider API keys, then run: codex-rosetta-gateway")


def _cmd_add_provider(args: argparse.Namespace) -> None:
    config_path = _config_path_for_write(args.config)
    data, path = _load_or_create_config(config_path)

    name: str = args.name
    default_key = f"${{{get_default_api_key_env(name)}}}"
    default_url = get_default_base_url(name)

    # api_key: CLI flag > interactive > auto-default
    api_key: str = args.api_key or ""
    if not api_key:
        if sys.stdin.isatty():
            api_key = input(
                f"API key env placeholder for '{name}' [{default_key}]: "
            ).strip()
        if not api_key:
            api_key = default_key

    # base_url: CLI flag > interactive > auto-default
    base_url: str = args.base_url or ""
    if not base_url:
        if default_url:
            base_url = default_url  # known provider — use default silently
        elif sys.stdin.isatty():
            base_url = input("Base URL (required): ").strip()
    if not base_url:
        print(
            "Error: --base-url is required for non-standard providers.", file=sys.stderr
        )
        sys.exit(1)

    data.setdefault("providers", {})[name] = {"api_key": api_key, "base_url": base_url}
    _write_jsonc(path, data)
    print(f"Added provider '{name}' to {path}")


def _cmd_add_model(args: argparse.Namespace) -> None:
    config_path = _config_path_for_write(args.config)
    data, path = _load_or_create_config(config_path)

    group_name: str = args.group
    groups = data.get("model_groups", {})
    group = groups.get(group_name) if isinstance(groups, dict) else None
    if not isinstance(group, dict):
        print(f"Error: model group '{group_name}' does not exist.", file=sys.stderr)
        sys.exit(1)
    model_name: str = args.name
    group.setdefault("models", {})[model_name] = {}
    _write_jsonc(path, data)
    print(f"Added model '{model_name}' to group '{group_name}' in {path}")


def _cmd_add_model_group(args: argparse.Namespace) -> None:
    """Add an empty model group owned by one provider."""
    config_path = _config_path_for_write(args.config)
    data, path = _load_or_create_config(config_path)
    providers = data.get("providers", {})
    if args.provider not in providers:
        print(f"Error: provider '{args.provider}' does not exist.", file=sys.stderr)
        sys.exit(1)
    groups = data.setdefault("model_groups", {})
    if args.name in groups:
        print(f"Error: model group '{args.name}' already exists.", file=sys.stderr)
        sys.exit(1)
    groups[args.name] = {
        "provider": args.provider,
        "type": "llm",
        **(
            {
                "tool_profile": default_tool_profile_for_provider(
                    providers[args.provider]
                )
            }
            if provider_supports_tool_profiles(providers[args.provider])
            else {}
        ),
        "models": {},
    }
    _write_jsonc(path, data)
    print(f"Added model group '{args.name}' to {path}")


def _cmd_clear_local_mode(args: argparse.Namespace) -> None:
    """Disable local mode and remove only the files/settings managed by Rosetta."""
    try:
        codex_home = resolve_codex_home(args.codex_home)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    config_path = _config_path_for_write(args.config)
    data, path = _load_or_create_config(config_path)
    server = data.setdefault("server", {})
    server["local_mode"] = False
    transaction = CodexLocalModeTransaction.clear(codex_home)
    try:
        write_config(path, data, activate=transaction.apply)
    except Exception as exc:
        try:
            transaction.rollback()
        except Exception as rollback_exc:
            print(
                f"Error: failed to clear local mode and rollback failed: {rollback_exc}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Error: failed to clear local mode: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Disabled local mode in {path}")
    print(f"Cleared Rosetta-managed Codex catalog settings under {codex_home}")


def _confirm_local_mode(codex_home: str) -> bool:
    message = (
        f"Local mode will write model_catalog.json under {codex_home}, configure "
        "the codex_rosetta provider in config.toml, and create a stable gateway "
        "API key named codex."
    )
    if config_toml_has_model_catalog(codex_home):
        message += " Existing model_catalog_json settings will be cleared."
    answer = input(f"{message} Continue? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _configure_local_mode_startup(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    config_path: str,
    codex_home: str,
) -> None:
    """Persist first-run state and synchronize Codex files before startup."""
    raw_config = load_config_raw(config_path)
    server = raw_config.setdefault("server", {})
    config_changed = False
    if args.local_mode or args.no_local_mode:
        requested_local_mode = args.local_mode
        config_changed = server.get("local_mode") is not requested_local_mode
        server["local_mode"] = requested_local_mode

    try:
        GatewayConfig.from_raw_with_env(raw_config)
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(f"invalid config: {exc}")

    local_mode_enabled = server.get("local_mode", True)
    local_mode_confirmed = server.get("local_mode_confirmed", False)
    if args.confirm_clear_existing_catalog and not local_mode_confirmed:
        server["local_mode_confirmed"] = True
        local_mode_confirmed = True
        config_changed = True

    if local_mode_enabled and not local_mode_confirmed:
        if not sys.stdin.isatty():
            parser.error(
                "local mode requires first-run confirmation in a non-interactive "
                "environment; pass --confirm-clear-existing-catalog or disable "
                "server.local_mode"
            )
        else:
            accepted = _confirm_local_mode(codex_home)

        if not accepted:
            server["local_mode"] = False
            write_config(config_path, raw_config)
            print("Local mode was disabled; Codex Home was not modified.")
            return
        server["local_mode"] = True
        server["local_mode_confirmed"] = True
        local_mode_confirmed = True
        config_changed = True

    if not local_mode_enabled:
        if config_changed:
            write_config(config_path, raw_config)
        return
    if not local_mode_confirmed:
        return

    try:
        if ensure_codex_api_key(raw_config):
            config_changed = True
        validated_config = GatewayConfig.from_raw_with_env(raw_config)
        api_key = codex_api_key_value(validated_config.api_keys)
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(f"invalid local mode config: {exc}")

    gateway_port = args.port if args.port is not None else validated_config.port
    transaction = CodexLocalModeTransaction.sync(
        codex_home,
        raw_config,
        gateway_port=gateway_port,
        api_key=api_key,
    )
    try:
        if config_changed:
            write_config(config_path, raw_config, activate=transaction.apply)
        else:
            transaction.apply()
    except Exception as exc:
        try:
            transaction.rollback()
        except Exception as rollback_exc:
            parser.error(
                f"failed to update Codex local mode and rollback failed: {rollback_exc}"
            )
        parser.error(f"failed to update Codex local mode: {exc}")


def _dispatch_command(
    args: argparse.Namespace,
    add_parser: argparse.ArgumentParser,
    local_mode_parser: argparse.ArgumentParser,
) -> bool:
    """Run a non-server CLI action and report whether startup should stop."""
    if args.edit:
        _open_in_editor(args.config)
        return True
    if args.command == "init":
        _cmd_init(args)
        return True
    if args.command == "add":
        if args.add_type == "provider":
            _cmd_add_provider(args)
        elif args.add_type == "model-group":
            _cmd_add_model_group(args)
        elif args.add_type == "model":
            _cmd_add_model(args)
        else:
            add_parser.print_help()
        return True
    if args.command == "local-mode":
        if args.local_mode_command == "clear":
            _cmd_clear_local_mode(args)
        else:
            local_mode_parser.print_help()
        return True
    return False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_KNOWN_PROVIDERS = known_provider_types()


def main() -> None:
    """Parse CLI arguments and either run a subcommand or start the server."""
    from .app import create_app

    parser = argparse.ArgumentParser(
        description="codex-rosetta Gateway — cross-provider LLM proxy",
    )
    parser.add_argument(
        "--config",
        "-c",
        default=None,
        help=(
            "Path to directory containing config.jsonc; initialized if missing "
            "(default: ~/.config/codex-rosetta-gateway)"
        ),
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--codex-home",
        default=None,
        help="Codex Home directory (default: $CODEX_HOME or ~/.codex)",
    )
    local_mode_group = parser.add_mutually_exclusive_group()
    local_mode_group.add_argument(
        "--local-mode",
        action="store_true",
        help="Enable local mode persistently in config.jsonc",
    )
    local_mode_group.add_argument(
        "--no-local-mode",
        action="store_true",
        help="Disable local mode persistently without modifying Codex Home",
    )
    parser.add_argument(
        "--confirm-clear-existing-catalog",
        action="store_true",
        help=(
            "Confirm that local mode may replace existing model_catalog_json "
            "settings; also enables non-interactive first startup"
        ),
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="Suppress the startup banner",
    )
    parser.add_argument(
        "--edit",
        "-e",
        action="store_true",
        help="Open the config file in $EDITOR for editing",
    )
    parser.add_argument("--host", default=None, help="Override server host")
    parser.add_argument("--port", type=int, default=None, help="Override server port")
    parser.add_argument(
        "--socket",
        "-S",
        default=None,
        help="Listen on a Unix domain socket instead of TCP (e.g. /run/user/1000/rosetta.sock)",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="HTTP/SOCKS proxy URL for upstream requests (overrides config)",
    )
    parser.add_argument(
        "--log-level",
        default="warning",
        choices=["info", "stats", "warning", "error"],
        help="Terminal log level (default: warning)",
    )

    # ``init`` subcommand
    sub = parser.add_subparsers(dest="command")
    sub.add_parser(
        "init",
        help="Create a template config.jsonc at ~/.config/codex-rosetta-gateway/",
    )

    # ``add`` subcommands
    add_parser = sub.add_parser(
        "add", help="Add a provider, model group, or grouped model to the config"
    )
    add_sub = add_parser.add_subparsers(dest="add_type")

    _provider_list = ", ".join(_KNOWN_PROVIDERS)
    prov_parser = add_sub.add_parser("provider", help="Add a provider entry")
    prov_parser.add_argument(
        "name",
        help=f"Provider type. Built-in types: {_provider_list}",
    )
    prov_parser.add_argument(
        "--api-key", default=None, help="API key or ${ENV_VAR} placeholder"
    )
    prov_parser.add_argument("--base-url", default=None, help="Provider base URL")

    group_parser = add_sub.add_parser("model-group", help="Add a model group")
    group_parser.add_argument("name", help="Model group name")
    group_parser.add_argument("--provider", required=True, help="Target provider name")

    model_parser = add_sub.add_parser("model", help="Add a model to a group")
    model_parser.add_argument("name", help="Model name (e.g. gpt-4o)")
    model_parser.add_argument("--group", required=True, help="Target model group")

    local_mode_parser = sub.add_parser(
        "local-mode", help="Manage Codex local-mode files and configuration"
    )
    local_mode_sub = local_mode_parser.add_subparsers(dest="local_mode_command")
    local_mode_sub.add_parser(
        "clear",
        help="Disable local mode and clear its managed catalog and TOML setting",
    )

    args = parser.parse_args()

    if _dispatch_command(args, add_parser, local_mode_parser):
        return

    # --- normal server startup ---
    if not args.no_banner:
        print_banner()

    try:
        codex_home = resolve_codex_home(args.codex_home)
    except ValueError as exc:
        parser.error(str(exc))
    os.environ[CODEX_HOME_ENV] = codex_home

    config_dir = args.config or DEFAULT_CONFIG_DIR
    config_path = config_path_for_dir(config_dir)
    if not os.path.isfile(config_path):
        _create_initial_config(config_path)

    _configure_local_mode_startup(parser, args, config_path, codex_home)

    runtime_config = load_config(config_path)

    # CLI --proxy overrides config-level server.proxy
    if args.proxy:
        runtime_config.setdefault("server", {})["proxy"] = args.proxy

    config = GatewayConfig(runtime_config)

    setup_logging(log_level=args.log_level)

    host = args.host or config.host
    port = args.port or config.port
    socket_path = args.socket or config.socket

    if config.local_mode and host.strip().lower() not in {"127.0.0.1", "localhost"}:
        print(
            "Remote use of this gateway requires manually configuring "
            "config.toml and model_catalog_json.",
            file=sys.stderr,
        )

    logger.info("Config loaded from %s", config_path)
    if socket_path:
        logger.info("Starting codex-rosetta gateway on unix:%s", socket_path)
    else:
        logger.info("Starting codex-rosetta gateway on %s:%d", host, port)
    logger.info("Configured providers: %s", list(config.providers.keys()))
    logger.info("Configured models: %s", list(config.models.keys()))
    logger.info("Codex Home: %s", codex_home)
    if config.log_bodies:
        logger.info(
            "Request/response body logging enabled on the dedicated DEBUG body "
            "logger (configured API tokens are redacted)"
        )

    app = create_app(
        config,
        config_path=config_path,
        codex_home=codex_home,
        gateway_port=port,
    )

    from .app import run_gateway

    try:
        asyncio.run(run_gateway(app, host, port, socket=socket_path))
    except KeyboardInterrupt:
        pass
