"""Run one isolated real GPT relay compatibility scenario."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from codex_rosetta.gateway.config import load_config_raw

from .evaluate import evaluate, read_events


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _new_run_root(root: Path) -> Path:
    run_id = datetime.now().strftime("%Y%m%d%H%M")
    run_root = root / "tmp" / "agent_testing_workspace" / run_id
    if run_root.exists():
        raise RuntimeError(
            f"isolated run root already exists for this minute: {run_root}; retry next minute"
        )
    for child in ("worktree", "codex_home", "gateway", "artifacts"):
        (run_root / child).mkdir(parents=True, exist_ok=False)
    return run_root


def _wait_ready(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"capture proxy exited early with {process.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("capture proxy did not become ready")


def _copy_fixture(root: Path, run_root: Path) -> str:
    suite = root / "tests" / "agent_workspace" / "command_execution"
    for source in (suite / "common", suite / "01"):
        shutil.copytree(source, run_root / "worktree", dirs_exist_ok=True)
    return (run_root / "worktree" / "TASK.md").read_text(encoding="utf-8")


def _write_codex_config(
    *, run_root: Path, port: int, model: str, provider_display_name: str
) -> None:
    worktree = str((run_root / "worktree").resolve())
    config = f"""model_provider = "relay"
model = {json.dumps(model)}
sandbox_mode = "danger-full-access"
approval_policy = "never"
model_reasoning_effort = "medium"
model_reasoning_summary = "concise"

[features]
concurrent_reasoning_summaries = true

[model_providers.relay]
name = {json.dumps(provider_display_name)}
wire_api = "responses"
requires_openai_auth = true
base_url = "http://127.0.0.1:{port}/v1"
experimental_bearer_token = "capture-proxy-client"

[projects.{json.dumps(worktree)}]
trust_level = "trusted"
"""
    (run_root / "codex_home" / "config.toml").write_text(config, encoding="utf-8")


def _run_direct(port: int, model: str, stdout_path: Path, stderr_path: Path) -> int:
    payload = json.dumps(
        {
            "model": model,
            "input": "Reply with exactly RELAY_OK.",
            "stream": True,
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/responses",
        data=payload,
        headers={
            "Authorization": "Bearer capture-proxy-client",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            stdout_path.write_bytes(response.read())
        stderr_path.write_text("", encoding="utf-8")
        return 0
    except Exception as exc:
        stderr_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        return 1


def _run_codex(
    *, run_root: Path, model: str, prompt: str, stdout_path: Path, stderr_path: Path
) -> int:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(run_root / "codex_home")
    command = [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "-C",
        str(run_root / "worktree"),
        "-m",
        model,
        prompt,
    ]
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            completed = subprocess.run(
                command, env=env, stdout=stdout, stderr=stderr, timeout=300, check=False
            )
            return completed.returncode
        except subprocess.TimeoutExpired:
            stderr.write(b"gpt relay scenario timed out after 300 seconds\n")
            return 124


def _run_harness(
    *, root: Path, scenario: str, port: int, model: str, run_root: Path
) -> int:
    env = os.environ.copy()
    codex_exe = shutil.which("codex")
    if codex_exe is None:
        raise RuntimeError("codex executable is required for the source harness")
    env.update(
        {
            "CARGO_BIN_EXE_codex": codex_exe,
            "CARGO_BIN_EXE_codex-code-mode-host": codex_exe,
            "GPT_RELAY_PROXY_URL": f"http://127.0.0.1:{port}/v1",
            "GPT_RELAY_MODEL": model,
            "GPT_RELAY_SCENARIO": scenario,
            "GPT_RELAY_CODEX_HOME": str(run_root / "codex_home"),
        }
    )
    command = [
        "cargo",
        "+1.95.0",
        "run",
        "--quiet",
        "--manifest-path",
        str(root / "tests/integration/gpt_relay/codex_harness/Cargo.toml"),
    ]
    with (
        (run_root / "artifacts/harness.json").open("wb") as stdout,
        (run_root / "artifacts/harness.stderr").open("wb") as stderr,
    ):
        try:
            completed = subprocess.run(
                command, env=env, stdout=stdout, stderr=stderr, timeout=900, check=False
            )
            return completed.returncode
        except subprocess.TimeoutExpired:
            stderr.write(b"Codex Rust harness timed out after 900 seconds\n")
            return 124


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario", choices=[f"C{i}" for i in range(6)], required=True
    )
    parser.add_argument("--provider-name", required=True)
    parser.add_argument("--gateway-config", type=Path, required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[3]
    raw = load_config_raw(str(args.gateway_config.expanduser()))
    if args.provider_name not in raw.get("providers", {}):
        parser.error(
            f"provider {args.provider_name!r} does not exist in gateway config"
        )
    run_root = _new_run_root(root)
    copied_config = run_root / "gateway/config.jsonc"
    shutil.copy2(args.gateway_config.expanduser(), copied_config)
    prompt = _copy_fixture(root, run_root)
    port = _free_port()
    capture_log = run_root / "artifacts/capture.jsonl"
    proxy_command = [
        sys.executable,
        "-m",
        "tests.integration.gpt_relay.capture_proxy",
        "--config",
        str(copied_config),
        "--provider-name",
        args.provider_name,
        "--model",
        args.model,
        "--log",
        str(capture_log),
        "--port",
        str(port),
    ]
    if args.scenario == "C4":
        proxy_command.append("--fail-old-model-compact")
        proxy_command.append("--normalize-zstd-upstream")
    proxy_stdout = (run_root / "gateway/proxy.stdout").open("wb")
    proxy_stderr = (run_root / "gateway/proxy.stderr").open("wb")
    proxy = subprocess.Popen(proxy_command, stdout=proxy_stdout, stderr=proxy_stderr)
    exit_code = 1
    try:
        _wait_ready(port, proxy)
        if args.scenario == "C0":
            exit_code = _run_direct(
                port,
                args.model,
                run_root / "artifacts/direct.sse",
                run_root / "artifacts/direct.stderr",
            )
        elif args.scenario in {"C1", "C2"}:
            display_name = args.provider_name if args.scenario == "C1" else "OpenAI"
            _write_codex_config(
                run_root=run_root,
                port=port,
                model=args.model,
                provider_display_name=display_name,
            )
            exit_code = _run_codex(
                run_root=run_root,
                model=args.model,
                prompt=prompt,
                stdout_path=run_root / "artifacts/codex.jsonl",
                stderr_path=run_root / "artifacts/codex.stderr",
            )
        else:
            exit_code = _run_harness(
                root=root,
                scenario=args.scenario,
                port=port,
                model=args.model,
                run_root=run_root,
            )
    finally:
        if proxy.poll() is None:
            proxy.send_signal(signal.SIGTERM)
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=5)
        proxy_stdout.close()
        proxy_stderr.close()

    result = evaluate(
        scenario=args.scenario,
        representative_provider=args.provider_name,
        model=args.model,
        events=read_events(capture_log),
        process_exit_code=exit_code,
    )
    result["run_root"] = str(run_root)
    result_path = run_root / "artifacts/evaluation.json"
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["classification"].startswith("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
