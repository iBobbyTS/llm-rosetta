#!/usr/bin/env python3
"""Run one isolated real-Codex context-compaction smoke test."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_rosetta.gateway.config import _strip_jsonc_comments


SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[2]
DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_TASK_ID = "02"
DEFAULT_TRIGGER = "manual"
AUTH_SOURCE = Path("/Users/ibobby/.codex-multi-2/auth.json")
GATEWAY_CONFIG_SOURCE = Path.home() / ".config/codex-rosetta-gateway/config.jsonc"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _check_ignored(*paths: Path) -> None:
    for path in paths:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "-q", str(path)],
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"secret destination is not git-ignored: {path}")


def _copy_task(run_root: Path, task_id: str) -> None:
    task = SUITE / task_id
    if not (task / "TASK.md").is_file() or not (task / "expected.json").is_file():
        raise ValueError(f"unknown context-compaction task: {task_id}")
    worktree = run_root / "worktree"
    shutil.copytree(SUITE / "common", worktree, dirs_exist_ok=True)
    shutil.copytree(task, worktree, dirs_exist_ok=True)


def _configure_run(
    run_root: Path,
    gateway_log_root: Path,
    *,
    model: str,
    port: int,
    auto_compact_token_limit: int,
) -> str:
    gateway_path = run_root / "gateway" / "config.jsonc"
    config = json.loads(_strip_jsonc_comments(gateway_path.read_text(encoding="utf-8")))
    server = config.setdefault("server", {})
    server["host"] = "127.0.0.1"
    server["port"] = port
    server["stream_trace"] = {
        "enabled": True,
        "filter": model,
        "path": str(gateway_log_root / "rosetta-trace.jsonl"),
    }
    api_keys = server.get("api_keys")
    if not isinstance(api_keys, list) or not api_keys:
        raise ValueError("copied gateway config has no server.api_keys")
    client_key = api_keys[0].get("key")
    if not isinstance(client_key, str) or not client_key:
        raise ValueError("copied gateway config has no usable client key")

    groups = config.get("model_groups")
    if not isinstance(groups, dict):
        raise ValueError("copied gateway config has no model_groups")
    matching_groups = [
        (name, group)
        for name, group in groups.items()
        if isinstance(group, dict) and model in group.get("models", {})
    ]
    if not matching_groups:
        raise ValueError(f"copied gateway config does not route model {model!r}")
    if not any(group.get("provider") == "Pixel (K12)" for _, group in matching_groups):
        raise ValueError(f"model {model!r} is not routed to Pixel (K12)")

    _write_json(gateway_path, config)
    codex_config = "\n".join(
        [
            'model_provider = "codex_rosetta"',
            f"model = {_toml_string(model)}",
            'sandbox_mode = "danger-full-access"',
            'approval_policy = "never"',
            'model_reasoning_effort = "medium"',
            f"model_auto_compact_token_limit = {auto_compact_token_limit}",
            "",
            "[model_providers.codex_rosetta]",
            'name = "OpenAI"',
            'wire_api = "responses"',
            "requires_openai_auth = true",
            f'base_url = "http://127.0.0.1:{port}/v1"',
            f"experimental_bearer_token = {_toml_string(client_key)}",
            "",
            f"[projects.{_toml_string(str(run_root / 'worktree'))}]",
            'trust_level = "trusted"',
            "",
        ]
    )
    (run_root / "codex_home" / "config.toml").write_text(
        codex_config,
        encoding="utf-8",
    )
    return client_key


def _codex_env(run_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(run_root / "codex_home")
    return env


def _validate_auth(run_root: Path, *, port: int, client_key: str) -> None:
    auth = json.loads(AUTH_SOURCE.read_text(encoding="utf-8"))
    if auth.get("auth_mode") != "chatgpt" or not isinstance(auth.get("tokens"), dict):
        raise RuntimeError("authorized Codex auth source is not ChatGPT OAuth")
    shutil.copy2(AUTH_SOURCE, run_root / "codex_home" / "auth.json")
    os.chmod(run_root / "codex_home" / "auth.json", 0o600)
    status = subprocess.run(
        ["codex", "login", "status"],
        check=False,
        capture_output=True,
        text=True,
        env=_codex_env(run_root),
    )
    if status.returncode != 0 or "ChatGPT" not in status.stdout + status.stderr:
        raise RuntimeError("isolated Codex Home did not report ChatGPT authentication")
    _write_json(
        run_root / "artifacts" / "runtime-auth.json",
        {
            "execution_mode": "oauth_plus_experimental_bearer_local_mode",
            "gateway_secret_source_directory": "~/.config/codex-rosetta-gateway",
            "auth_source": str(AUTH_SOURCE),
            "codex_login_status": "chatgpt_oauth",
            "gateway_mode": "local_mode",
            "provider_identity": "codex_rosetta",
            "provider_display_name": "OpenAI",
            "provider_requires_openai_auth": True,
            "provider_bearer_present": bool(client_key),
            "provider_base_url": f"http://127.0.0.1:{port}/v1",
        },
    )


def _wait_ready(port: int, client_key: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/models",
        headers={"Authorization": f"Bearer {client_key}"},
    )
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"isolated gateway exited with {process.returncode}")
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError, urllib.error.URLError:
            time.sleep(0.2)
    raise TimeoutError("isolated gateway did not become ready")


def _contains_item_type(value: Any, item_type: str) -> bool:
    if isinstance(value, dict):
        if value.get("type") == item_type:
            return True
        return any(_contains_item_type(child, item_type) for child in value.values())
    if isinstance(value, list):
        return any(_contains_item_type(child, item_type) for child in value)
    return False


def _trace_result(path: Path) -> dict[str, Any]:
    starts: dict[str, bool] = {}
    trigger_ids: list[str] = []
    followup_ids: list[str] = []
    errors: dict[str, int] = {}
    models: set[str] = set()
    if not path.is_file():
        return {
            "trace_present": False,
            "models": [],
            "trigger_request_count": 0,
            "trigger_wire_passthrough": [],
            "followup_compaction_input_observed": False,
            "trigger_upstream_errors": [],
        }
    for line in path.open(encoding="utf-8"):
        event = json.loads(line)
        request_id = str(event.get("request_id", ""))
        model = event.get("model")
        if isinstance(model, str):
            models.add(model)
        stage = event.get("stage")
        data = event.get("data")
        if stage == "stream_start" and isinstance(data, dict):
            starts[request_id] = data.get("wire_passthrough") is True
        elif stage == "raw_passthrough_request":
            if _contains_item_type(data, "compaction_trigger"):
                trigger_ids.append(request_id)
            elif _contains_item_type(data, "compaction"):
                followup_ids.append(request_id)
        elif stage == "upstream_error" and isinstance(data, dict):
            status = data.get("status_code")
            if isinstance(status, int):
                errors[request_id] = status
    return {
        "trace_present": True,
        "models": sorted(models),
        "trigger_request_count": len(trigger_ids),
        "trigger_wire_passthrough": [starts.get(item, False) for item in trigger_ids],
        "followup_compaction_input_observed": bool(followup_ids),
        "trigger_upstream_errors": [
            errors[item] for item in trigger_ids if item in errors
        ],
    }


def _request_profiles(run_root: Path) -> list[dict[str, Any]]:
    databases = list((run_root / "gateway").rglob("gateway.db"))
    if len(databases) != 1:
        return []
    with sqlite3.connect(databases[0]) as connection:
        rows = connection.execute(
            "SELECT profile FROM request_log WHERE profile LIKE '%compaction_mode%'"
        ).fetchall()
    return [json.loads(profile) for (profile,) in rows]


def _run_codex(run_root: Path, timeout_seconds: int) -> tuple[int, str | None]:
    prompt = (run_root / "worktree" / "TASK.md").read_text(encoding="utf-8")
    stdout_path = run_root / "artifacts" / "codex.jsonl"
    stderr_path = run_root / "artifacts" / "codex.stderr"
    final_path = run_root / "artifacts" / "final.txt"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            completed = subprocess.run(
                [
                    "codex",
                    "exec",
                    "--json",
                    "--skip-git-repo-check",
                    "-C",
                    str(run_root / "worktree"),
                    "-o",
                    str(final_path),
                    prompt,
                ],
                check=False,
                stdout=stdout,
                stderr=stderr,
                env=_codex_env(run_root),
                timeout=timeout_seconds,
            )
            return completed.returncode, None
        except subprocess.TimeoutExpired:
            return 124, f"codex timed out after {timeout_seconds} seconds"


class _AppServerClient:
    def __init__(self, run_root: Path, timeout_seconds: int) -> None:
        self.protocol_path = run_root / "artifacts" / "app-server.jsonl"
        self.deadline = time.monotonic() + timeout_seconds
        self.messages: list[dict[str, Any]] = []
        self._stderr = (run_root / "artifacts" / "app-server.stderr").open("wb")
        self.process = subprocess.Popen(
            ["codex", "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            env=_codex_env(run_root),
            text=True,
            bufsize=1,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("failed to open Codex app-server stdio")
        self._attestation = subprocess.Popen(
            ["node", str(SUITE / "devicecheck_attestation.js")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        ready = self._read_attestation_message()
        if ready != {"ready": True}:
            raise RuntimeError("DeviceCheck attestation helper did not become ready")

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def receive(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        while time.monotonic() < self.deadline:
            line = self.process.stdout.readline()
            if line:
                message = json.loads(line)
                self.messages.append(message)
                with self.protocol_path.open("a", encoding="utf-8") as artifact:
                    artifact.write(json.dumps(message, ensure_ascii=False) + "\n")
                return message
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"Codex app-server exited with {self.process.returncode}"
                )
        raise TimeoutError("Codex app-server protocol timed out")

    def _read_attestation_message(self) -> dict[str, Any]:
        if self._attestation.stdout is None:
            raise RuntimeError("DeviceCheck attestation helper stdout is unavailable")
        line = self._attestation.stdout.readline()
        if not line:
            raise RuntimeError("DeviceCheck attestation helper disconnected")
        message = json.loads(line)
        if not isinstance(message, dict):
            raise RuntimeError("DeviceCheck attestation helper returned invalid output")
        return message

    def _generate_attestation(self) -> str:
        if self._attestation.stdin is None:
            raise RuntimeError("DeviceCheck attestation helper stdin is unavailable")
        self._attestation.stdin.write("generate\n")
        self._attestation.stdin.flush()
        message = self._read_attestation_message()
        token = message.get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError(
                f"DeviceCheck attestation generation failed: {message.get('error')}"
            )
        return token

    def _handle_server_request(self, message: dict[str, Any]) -> bool:
        if message.get("method") != "attestation/generate" or "id" not in message:
            return False
        self.send(
            {
                "id": message["id"],
                "result": {"token": self._generate_attestation()},
            }
        )
        return True

    def request(self, request_id: int, method: str, params: dict[str, Any]) -> Any:
        self.send({"method": method, "id": request_id, "params": params})
        while True:
            message = self.receive()
            if self._handle_server_request(message):
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            return message.get("result")

    def wait_for_turn(self, thread_id: str) -> dict[str, Any]:
        while True:
            message = self.receive()
            if self._handle_server_request(message):
                continue
            if message.get("method") != "turn/completed":
                continue
            params = message.get("params")
            if isinstance(params, dict) and params.get("threadId") == thread_id:
                return params

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self._attestation.terminate()
        try:
            self._attestation.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._attestation.kill()
            self._attestation.wait(timeout=5)
        self._stderr.close()


def _require_completed_turn(params: dict[str, Any], label: str) -> None:
    turn = params.get("turn")
    if not isinstance(turn, dict) or turn.get("status") != "completed":
        raise RuntimeError(f"{label} turn did not complete")


def _run_manual_compact(
    run_root: Path, timeout_seconds: int, success_marker: str
) -> tuple[int, str | None]:
    client = _AppServerClient(run_root, timeout_seconds)

    try:
        client.request(
            1,
            "initialize",
            {
                "clientInfo": {
                    "name": "Codex Desktop",
                    "title": "Codex Desktop",
                    "version": "1.0.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "requestAttestation": True,
                },
            },
        )
        client.send({"method": "initialized", "params": {}})
        started = client.request(
            2,
            "thread/start",
            {
                "cwd": str(run_root / "worktree"),
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
            },
        )
        thread = started.get("thread") if isinstance(started, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("thread/start did not return a thread id")
        client.request(
            3,
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": "Reply only READY:MANUAL_COMPACT"}],
            },
        )
        _require_completed_turn(client.wait_for_turn(thread_id), "seed")
        client.request(4, "thread/compact/start", {"threadId": thread_id})
        _require_completed_turn(client.wait_for_turn(thread_id), "manual compact")
        client.request(
            5,
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": f"Reply only {success_marker}"}],
            },
        )
        _require_completed_turn(client.wait_for_turn(thread_id), "post-compact")
        observed = success_marker in json.dumps(client.messages, ensure_ascii=False)
        (run_root / "artifacts" / "final.txt").write_text(
            success_marker if observed else "",
            encoding="utf-8",
        )
        return (0, None) if observed else (1, "success marker not observed")
    except TimeoutError as exc:
        return 124, str(exc)
    except RuntimeError as exc:
        return 1, str(exc)
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument(
        "--trigger",
        choices=("manual", "context-limit"),
        default=DEFAULT_TRIGGER,
    )
    parser.add_argument("--timeout-seconds", type=int)
    args = parser.parse_args()

    expected = json.loads(
        (SUITE / args.task_id / "expected.json").read_text(encoding="utf-8")
    )
    timeout_seconds = args.timeout_seconds or int(expected["timeout_seconds"])
    run_id = datetime.now().astimezone().strftime("%Y%m%d%H%M")
    run_root = ROOT / "tmp" / "agent_testing_workspace" / run_id
    gateway_log_root = Path("/Volumes/RAMDisk") / run_id
    if run_root.exists() or gateway_log_root.exists():
        raise RuntimeError(f"timestamped run root already exists: {run_id}")
    for directory in (
        run_root / "worktree",
        run_root / "codex_home",
        run_root / "gateway",
        run_root / "artifacts",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    _copy_task(run_root, args.task_id)

    gateway_path = run_root / "gateway" / "config.jsonc"
    auth_path = run_root / "codex_home" / "auth.json"
    _check_ignored(gateway_path, auth_path)
    shutil.copy2(GATEWAY_CONFIG_SOURCE, gateway_path)
    gateway_log_root.mkdir(parents=True)
    (run_root / "artifacts" / "gateway-log-root.txt").write_text(
        str(gateway_log_root) + "\n",
        encoding="utf-8",
    )

    port = _free_port()
    client_key = _configure_run(
        run_root,
        gateway_log_root,
        model=args.model,
        port=port,
        auto_compact_token_limit=int(expected["model_auto_compact_token_limit"]),
    )
    _validate_auth(run_root, port=port, client_key=client_key)

    stdout = (run_root / "gateway" / "stdout.log").open("wb")
    stderr = (run_root / "gateway" / "stderr.log").open("wb")
    gateway = subprocess.Popen(
        [
            "codex-rosetta-gateway",
            "--config",
            str(run_root / "gateway"),
            "--codex-home",
            str(run_root / "codex_home"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-banner",
            "--local-mode",
            "--confirm-clear-existing-catalog",
        ],
        stdout=stdout,
        stderr=stderr,
    )
    (run_root / "gateway" / "pid").write_text(str(gateway.pid) + "\n")
    codex_exit = 1
    runner_error: str | None = None
    try:
        _wait_ready(port, client_key, gateway)
        if args.trigger == "manual":
            codex_exit, runner_error = _run_manual_compact(
                run_root,
                timeout_seconds,
                expected["success_marker"],
            )
        else:
            codex_exit, runner_error = _run_codex(run_root, timeout_seconds)
    finally:
        gateway.terminate()
        try:
            gateway.wait(timeout=10)
        except subprocess.TimeoutExpired:
            gateway.kill()
            gateway.wait(timeout=5)
        stdout.close()
        stderr.close()

    final_path = run_root / "artifacts" / "final.txt"
    final_text = final_path.read_text(encoding="utf-8") if final_path.is_file() else ""
    trace = _trace_result(gateway_log_root / "rosetta-trace.jsonl")
    profiles = _request_profiles(run_root)
    native_profiles = [
        profile
        for profile in profiles
        if profile.get("compaction_mode") == "native"
        and profile.get("compaction_reason")
        == ("user_requested" if args.trigger == "manual" else "context_limit")
    ]
    success = (
        codex_exit == 0
        and expected["success_marker"] in final_text
        and trace["trigger_request_count"] == 1
        and trace["trigger_wire_passthrough"] == [True]
        and not trace["trigger_upstream_errors"]
        and trace["followup_compaction_input_observed"]
        and len(native_profiles) == 1
        and native_profiles[0].get("wire_passthrough") is True
    )
    if success:
        classification = "completed"
    elif trace["trigger_upstream_errors"]:
        classification = "remote_compaction_error_reproduced"
    elif trace["trigger_request_count"] == 0:
        classification = "not_triggered"
    else:
        classification = "infrastructure_failure"
    result = {
        "suite": "context_compaction",
        "task_id": args.task_id,
        "trigger": args.trigger,
        "classification": classification,
        "success": success,
        "model": args.model,
        "model_substitution": args.model != expected.get("default_model"),
        "gateway_provider": "Pixel (K12)",
        "codex_model_provider": "codex_rosetta",
        "codex_exit_code": codex_exit,
        "success_marker_observed": expected["success_marker"] in final_text,
        "runner_error": runner_error,
        **trace,
        "native_compaction_profile_count": len(native_profiles),
        "native_profile_wire_passthrough": [
            profile.get("wire_passthrough") is True for profile in native_profiles
        ],
    }
    _write_json(run_root / "artifacts" / "automation-result.json", result)
    print(run_root)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
