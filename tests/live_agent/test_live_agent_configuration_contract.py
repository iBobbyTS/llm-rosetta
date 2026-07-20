from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from codex_rosetta.gateway.live_gate import (
    LIVE_CALL_APPROVAL_ENV,
    LIVE_CALL_APPROVAL_VALUE,
    require_live_call_approval,
)


LIVE_AGENT = Path(__file__).parent
REPO_ROOT = LIVE_AGENT.parents[1]

LIVE_EXAMPLES = tuple(
    sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for pattern in ("examples/rest_based/*.py", "examples/sdk_based/*.py")
        for path in REPO_ROOT.glob(pattern)
    )
)


def test_live_call_gate_fails_closed_without_developer_approval(monkeypatch) -> None:
    monkeypatch.delenv(LIVE_CALL_APPROVAL_ENV, raising=False)
    with pytest.raises(RuntimeError, match="live API calls are disabled"):
        require_live_call_approval()


def test_live_call_gate_accepts_only_explicit_non_secret_marker(monkeypatch) -> None:
    monkeypatch.setenv(LIVE_CALL_APPROVAL_ENV, LIVE_CALL_APPROVAL_VALUE)
    require_live_call_approval()


@pytest.mark.parametrize(
    ("relative_path", "first_sensitive_operation"),
    [
        ("tests/integration/test_anthropic_rest_e2e.py", "dotenv.load_dotenv"),
        ("tests/integration/test_anthropic_sdk_e2e.py", "dotenv.load_dotenv"),
        ("tests/integration/test_google_genai_rest_e2e.py", "dotenv.load_dotenv"),
        ("tests/integration/test_google_genai_sdk_e2e.py", "dotenv.load_dotenv"),
        ("tests/integration/test_openai_chat_rest_e2e.py", "dotenv.load_dotenv"),
        ("tests/integration/test_openai_chat_sdk_e2e.py", "dotenv.load_dotenv"),
        ("tests/integration/test_openai_responses_rest_e2e.py", "dotenv.load_dotenv"),
        ("tests/integration/test_openai_responses_sdk_e2e.py", "dotenv.load_dotenv"),
        ("tests/integration/test_gateway_agentabi.py", "from agentabi import run_sync"),
        ("tests/integration/gpt_relay/run.py", "raw = load_config_raw("),
        ("tests/integration/gpt_relay/capture_proxy.py", "raw = load_config_raw("),
    ],
)
def test_every_python_live_entrypoint_gates_before_sensitive_work(
    relative_path: str, first_sensitive_operation: str
) -> None:
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    assert (
        "from codex_rosetta.gateway.live_gate import require_live_call_approval"
        in source
    )
    assert source.index("require_live_call_approval()") < source.index(
        first_sensitive_operation
    )


@pytest.mark.parametrize("relative_path", LIVE_EXAMPLES)
def test_every_live_example_gates_before_dotenv(relative_path: str) -> None:
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    assert (
        "from codex_rosetta.gateway.live_gate import require_live_call_approval"
        in source
    )
    assert source.index("require_live_call_approval()") < source.index("load_dotenv()")


@pytest.mark.parametrize(
    "relative_path",
    [
        "scripts/run_gateway_integration.sh",
        "scripts/rosetta-test-codex.sh",
        "scripts/rosetta-test-claude-code.sh",
        "scripts/rosetta-test-opencode.sh",
    ],
)
def test_every_shell_live_entrypoint_uses_shared_gate(relative_path: str) -> None:
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    gate = '. "$SCRIPT_DIR/require_live_call_approval.sh"'

    assert gate in source
    sensitive_offsets = [
        offset
        for marker in ("API_KEY=", "exec codex", "exec claude", "exec opencode")
        if (offset := source.find(marker)) >= 0
    ]
    assert sensitive_offsets
    assert source.index(gate) < min(sensitive_offsets)


def test_shell_live_gate_fails_closed_and_accepts_only_exact_marker() -> None:
    gate = REPO_ROOT / "scripts/require_live_call_approval.sh"
    environment = os.environ.copy()
    environment.pop(LIVE_CALL_APPROVAL_ENV, None)

    blocked = subprocess.run(
        ["bash", str(gate)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert blocked.returncode == 2
    assert "live API calls are disabled by default" in blocked.stderr

    environment[LIVE_CALL_APPROVAL_ENV] = LIVE_CALL_APPROVAL_VALUE
    approved = subprocess.run(
        ["bash", str(gate)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert approved.returncode == 0


def _runtime_contract() -> dict[str, Any]:
    return json.loads(
        (LIVE_AGENT / "runtime-contract.json").read_text(encoding="utf-8")
    )


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_declared_codex_providers_use_local_mode_identity() -> None:
    observed_keys: set[str] = set()

    for path in LIVE_AGENT.glob("*/[0-9][0-9]/expected.json"):
        expected = json.loads(path.read_text(encoding="utf-8"))
        for mapping in _walk(expected):
            if "provider_identity" in mapping:
                observed_keys.add("provider_identity")
                assert mapping["provider_identity"] == "codex_rosetta", path
            if "provider_identities" in mapping:
                observed_keys.add("provider_identities")
                assert mapping["provider_identities"] == ["codex_rosetta"], path
            if "provider_display_name" in mapping:
                observed_keys.add("provider_display_name")
                assert mapping["provider_display_name"] == "OpenAI", path
            if "codex_model_provider" in mapping:
                observed_keys.add("codex_model_provider")
                assert mapping["codex_model_provider"] == "codex_rosetta", path

    assert observed_keys == {
        "provider_identity",
        "provider_identities",
        "provider_display_name",
        "codex_model_provider",
    }


def test_gateway_backed_cells_require_dual_auth_local_mode() -> None:
    contract = _runtime_contract()

    assert contract["scope"] == "all_gateway_backed_live_agent_cells"
    assert contract["execution_mode"] == "oauth_plus_experimental_bearer_local_mode"
    assert contract["gateway_mode"] == "local_mode"
    assert (
        contract["gateway_secret_source_directory"] == "~/.config/codex-rosetta-gateway"
    )
    assert contract["auth_source"] == "/Users/ibobby/.codex-multi-2/auth.json"
    assert contract["codex_auth_mode"] == "chatgpt_oauth"
    assert contract["provider_identity"] == "codex_rosetta"
    assert contract["provider_display_name"] == "OpenAI"
    assert contract["provider_requires_openai_auth"] is True
    assert contract["provider_request_auth"] == "experimental_bearer_token"
    assert contract["provider_base_url"] == "isolated_localhost_gateway"
    assert contract["model_requests_must_reach_isolated_gateway"] is True
    assert (
        contract["gpt_upstream_provider_policy"]
        == "any_configured_provider_that_responds"
    )
    assert contract["unreachable_provider_action"] == "stop_and_request_user_decision"
    assert contract["credential_free_artifact"] == "artifacts/runtime-auth.json"
    assert contract["credential_free_artifact_required_fields"] == [
        "execution_mode",
        "gateway_secret_source_directory",
        "auth_source",
        "codex_login_status",
        "gateway_mode",
        "provider_identity",
        "provider_display_name",
        "provider_requires_openai_auth",
        "provider_bearer_present",
        "provider_base_url",
    ]
    assert contract["credential_free_artifact_forbidden_fields"] == [
        "oauth_tokens",
        "api_key",
        "bearer_token",
        "authorization",
        "cookie",
        "copied_config",
    ]
    assert contract["secret_destinations_must_be_git_ignored"] is True
    assert contract["secret_values_must_not_enter_git_history"] is True
    assert contract["non_gateway_suite_exceptions"] == ["browser_use"]


def test_compaction_contract_requires_remote_v2_request_kind() -> None:
    paths = [
        *LIVE_AGENT.glob("context_compaction/[0-9][0-9]/expected.json"),
        *LIVE_AGENT.glob("context_compaction_summary_quality/[0-9][0-9]/expected.json"),
    ]

    assert len(paths) == 7
    for path in paths:
        expected = json.loads(path.read_text(encoding="utf-8"))
        assert expected["expected_request_kind"] == "remote_v2_in_band", path


def test_context_limit_compaction_retains_enough_command_output() -> None:
    for task_id in ("01", "02", "05"):
        expected = json.loads(
            (LIVE_AGENT / "context_compaction" / task_id / "expected.json").read_text(
                encoding="utf-8"
            )
        )
        assert expected["model_auto_compact_token_limit"] == 19_000
        assert expected["command_max_output_tokens_min"] >= 20_000
        assert expected["retained_command_output_chars_min"] >= 60_000


def test_compaction_protocol_and_exactly_once_scopes_are_separate() -> None:
    suite = LIVE_AGENT / "context_compaction"
    for task_id in ("01", "02"):
        expected = json.loads(
            (suite / task_id / "expected.json").read_text(encoding="utf-8")
        )
        assert expected["target_scope"] == "remote_compaction_protocol"
        assert expected["required_complete_protocol_chains_min"] == 1
        assert expected["expected_command_starts_min"] == 1
        assert "expected_command_starts" not in expected

    exactly_once = json.loads(
        (suite / "05" / "expected.json").read_text(encoding="utf-8")
    )
    assert exactly_once["target_scope"] == "post_compaction_exactly_once"
    assert exactly_once["expected_command_starts"] == 1
    assert exactly_once["expected_compaction_count"] == 1
    assert exactly_once["expected_rosetta_mapping_rows"] == 1


def test_gpt_live_cells_do_not_pin_an_upstream_gateway_provider() -> None:
    expected_paths = {
        "02": ("gateway_provider",),
        "03": ("first_gateway_provider",),
        "04": ("resume_gateway_provider",),
        "quality": ("gateway_provider",),
    }
    for task_id, fields in expected_paths.items():
        path = (
            LIVE_AGENT / "context_compaction_summary_quality" / "01" / "expected.json"
            if task_id == "quality"
            else LIVE_AGENT / "context_compaction" / task_id / "expected.json"
        )
        expected = json.loads(path.read_text(encoding="utf-8"))
        assert all(expected[field] is None for field in fields), path


def test_skill_delivery_contracts_use_separate_runners() -> None:
    namespace_expected = json.loads(
        (LIVE_AGENT / "namespace_tools" / "01" / "expected.json").read_text(
            encoding="utf-8"
        )
    )
    local_skill_expected = json.loads(
        (LIVE_AGENT / "local_skills" / "01" / "expected.json").read_text(
            encoding="utf-8"
        )
    )
    orchestrator_expected = json.loads(
        (LIVE_AGENT / "orchestrator_skills" / "01" / "expected.json").read_text(
            encoding="utf-8"
        )
    )

    assert namespace_expected["required_runner"] == "codex_exec_local"
    assert namespace_expected["local_execution_environment_attached"] is True
    assert "skills.list" not in namespace_expected["expected_native_pattern"]

    assert local_skill_expected["required_runner"] == "codex_exec_local"
    assert local_skill_expected["local_execution_environment_attached"] is True
    assert (
        local_skill_expected["expected_native_pattern"]["skills_namespace_calls_max"]
        == 0
    )
    local_task = (LIVE_AGENT / "local_skills" / "01" / "TASK.md").read_text(
        encoding="utf-8"
    )
    local_fixture = (
        LIVE_AGENT
        / "local_skills"
        / "01"
        / ".agents"
        / "skills"
        / "local-skill-fixture"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "RESULT:LOCAL_SKILL_OK" not in local_task
    assert "RESULT:LOCAL_SKILL_OK" in local_fixture

    assert orchestrator_expected["required_runner"] == "app_server_orchestrator"
    assert orchestrator_expected["local_execution_environment_attached"] is False
    assert orchestrator_expected["orchestrator_skills_enabled"] is True
    assert orchestrator_expected["orchestrator_provider_required"] is True
    assert orchestrator_expected["expected_native_pattern"]["skills.list"] == 1
    assert orchestrator_expected["expected_native_pattern"]["skills.read"] == 1


def test_image_generation_contract_requires_codex_auth_gate() -> None:
    runtime_contract = _runtime_contract()
    expected = json.loads(
        (LIVE_AGENT / "image_generation" / "01" / "expected.json").read_text(
            encoding="utf-8"
        )
    )

    assert (
        expected["mandatory_prerequisites"]["codex_image_generation_auth_gate"]
        == "passed"
    )
    assert (
        expected["mandatory_prerequisites"]["auth_source"]
        == runtime_contract["auth_source"]
    )
    assert expected["mandatory_prerequisites"]["codex_auth_mode"] == "chatgpt_oauth"
    assert (
        expected["mandatory_prerequisites"]["provider_request_auth"]
        == runtime_contract["provider_request_auth"]
    )


def test_file_workflow_records_route_specific_tool_selection() -> None:
    expected = json.loads(
        (LIVE_AGENT / "builtin_tools" / "03" / "expected.json").read_text(
            encoding="utf-8"
        )
    )

    observations = expected["route_tool_observations"]
    assert observations["openai_chat"]["localized_tools_to_check"] == [
        "Glob",
        "Grep",
        "Read",
        "Edit",
        "Write",
    ]
    assert observations["openai_responses"]["native_apply_patch_to_check"] is True
