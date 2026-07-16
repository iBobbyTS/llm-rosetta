"""Tests for the Codex source compatibility contract extractor."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

from codex_rosetta.gateway.codex_compaction import COMPACT_PROMPT, SUMMARY_PREFIX

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = (
    REPO_ROOT / "docs" / "dev" / "version-compatibility" / "codex-source-contract.json"
)
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_codex_compatibility.py"
SCRIPT = runpy.run_path(str(SCRIPT_PATH))

_enum_variants = SCRIPT["_enum_variants"]
_enum_variant_field_contracts = SCRIPT["_enum_variant_field_contracts"]
_responses_lite_model_fields = SCRIPT["_responses_lite_model_fields"]
_serde_enum_wire_types = SCRIPT["_serde_enum_wire_types"]
_struct_field_contracts = SCRIPT["_struct_field_contracts"]
_struct_fields = SCRIPT["_struct_fields"]
compare_snapshots = SCRIPT["compare_snapshots"]
classify_snapshots = SCRIPT["classify_snapshots"]
render_classification = SCRIPT["render_classification"]
snapshot_json = SCRIPT["snapshot_json"]


def test_rust_extractor_ignores_braces_in_comments_and_strings():
    source = r"""
pub struct Demo {
    pub alpha: String,
    /* } nested /* { */ comment */
    pub beta: &'static str,
    #[doc = r##"}"##]
    pub raw: &'static str,
}

pub struct Later {
    pub excluded: bool,
}
"""

    assert _struct_fields(source, "Demo") == ["alpha", "beta", "raw"]


def test_enum_extractors_capture_variants_and_wire_renames():
    source = """
pub enum ResponseItem {
    Message { text: String },
    FunctionCall(String),
    Other,
}

pub enum ToolSpec {
    #[serde(rename = "function")]
    Function(FunctionTool),
    #[serde(rename = "tool_search")]
    ToolSearch(ToolSearchTool),
}
"""

    assert _enum_variants(source, "ResponseItem") == [
        "FunctionCall",
        "Message",
        "Other",
    ]
    assert _serde_enum_wire_types(source, "ToolSpec") == {
        "function": "Function",
        "tool_search": "ToolSearch",
    }


def test_field_contracts_capture_types_and_attributes():
    source = """
pub struct ModelMessages {
    pub instructions_template: Option<String>,
    pub approvals: Option<ApprovalMessages>,
}

pub enum ResponseItem {
    AdditionalTools {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        id: Option<String>,
        role: String,
        tools: Vec<serde_json::Value>,
    },
}
"""

    assert _struct_field_contracts(source, "ModelMessages") == {
        "approvals": {"attributes": [], "type": "Option<ApprovalMessages>"},
        "instructions_template": {"attributes": [], "type": "Option<String>"},
    }
    assert _enum_variant_field_contracts(source, "ResponseItem", "AdditionalTools") == {
        "id": {
            "attributes": [
                '#[serde(default, skip_serializing_if = "Option::is_none")]'
            ],
            "type": "Option<String>",
        },
        "role": {"attributes": [], "type": "String"},
        "tools": {"attributes": [], "type": "Vec<serde_json::Value>"},
    }


def test_responses_lite_snapshot_keeps_stable_capability_subset():
    models_json = json.dumps(
        {
            "models": [
                {
                    "slug": "regular",
                    "use_responses_lite": False,
                },
                {
                    "slug": "lite-z",
                    "tool_mode": "code_mode_only",
                    "multi_agent_version": "v2",
                    "use_responses_lite": True,
                    "input_modalities": ["text", "image"],
                    "supports_parallel_tool_calls": True,
                    "supports_search_tool": True,
                    "supported_reasoning_levels": [
                        {"effort": "medium", "description": "ignored text"},
                        {"effort": "ultra", "description": "also ignored"},
                    ],
                    "default_reasoning_level": "medium",
                    "supports_reasoning_summaries": True,
                    "default_reasoning_summary": "none",
                    "web_search_tool_type": "text_and_image",
                    "apply_patch_tool_type": "freeform",
                    "base_instructions": "volatile and intentionally omitted",
                },
                {
                    "slug": "lite-a",
                    "tool_mode": "direct",
                    "multi_agent_version": "v1",
                    "use_responses_lite": True,
                    "input_modalities": ["text"],
                    "supports_parallel_tool_calls": False,
                    "supports_search_tool": False,
                    "supported_reasoning_levels": [{"effort": "low"}],
                    "default_reasoning_level": "low",
                    "supports_reasoning_summaries": False,
                    "default_reasoning_summary": "none",
                    "web_search_tool_type": "text",
                    "apply_patch_tool_type": None,
                },
            ]
        }
    )

    snapshot = _responses_lite_model_fields(models_json)

    assert [model["slug"] for model in snapshot] == ["lite-a", "lite-z"]
    assert snapshot[1]["supported_reasoning_levels"] == ["medium", "ultra"]
    assert snapshot[1]["tool_mode"] == "code_mode_only"
    assert snapshot[1]["multi_agent_version"] == "v2"
    assert "base_instructions" not in snapshot[1]


def test_snapshot_comparison_can_separate_contract_drift_from_commit_change():
    baseline = {
        "schema_version": 1,
        "codex_source_commit": "old",
        "contract": {"message_phase_variants": ["Commentary", "FinalAnswer"]},
    }
    commit_only = {**baseline, "codex_source_commit": "new"}
    contract_change = {
        **commit_only,
        "contract": {
            "message_phase_variants": ["Commentary", "FinalAnswer", "Progress"]
        },
    }

    assert compare_snapshots(baseline, commit_only)
    assert compare_snapshots(baseline, commit_only, check_source_commit=False) == ""
    assert compare_snapshots(baseline, contract_change, check_source_commit=False)


def test_checked_in_baseline_uses_canonical_serialization():
    baseline_text = BASELINE_PATH.read_text(encoding="utf-8")
    baseline = json.loads(baseline_text)

    assert baseline_text == snapshot_json(baseline)
    assert baseline["schema_version"] == 1
    assert baseline["codex_source_commit"]


def test_bundled_remote_compaction_prompts_match_the_reviewed_codex_source():
    source_root = REPO_ROOT.parent / "openai-codex-src"
    assert COMPACT_PROMPT == (
        source_root / "codex-rs/prompts/templates/compact/prompt.md"
    ).read_text(encoding="utf-8")
    assert SUMMARY_PREFIX == (
        source_root / "codex-rs/prompts/templates/compact/summary_prefix.md"
    ).read_text(encoding="utf-8")


def test_snapshot_classification_always_uses_three_result_categories():
    baseline = {
        "schema_version": 1,
        "codex_source_commit": "same",
        "contract": {
            "endpoints": {"RESPONSES_ENDPOINT": "/responses"},
            "model_info_fields": ["slug", "tool_mode"],
        },
    }

    classification = classify_snapshots(baseline, baseline)
    rendered = render_classification(classification)

    assert any(
        "codex_source_commit" in item
        for item in classification["high_confidence_unchanged"]
    )
    assert any(
        "contract.endpoints" in item
        for item in classification["high_confidence_unchanged"]
    )
    assert any(
        "contract.model_info_fields" in item
        for item in classification["possibly_unchanged"]
    )
    assert classification["changed"] == []
    assert "High-confidence unchanged:" in rendered
    assert "Possibly unchanged:" in rendered
    assert "Changed:\n  - None" in rendered


def test_new_complete_value_contracts_are_high_confidence():
    complete_value_contracts = {
        "approval_messages_fields": {"on_request": {"type": "Option<String>"}},
        "model_messages_fields": {"approvals": {"type": "Option<ApprovalMessages>"}},
        "response_item_additional_tools_fields": {
            "tools": {"type": "Vec<serde_json::Value>"}
        },
        "responses_lite_model_fields": [
            {
                "slug": "gpt-test",
                "tool_mode": "code_mode_only",
                "multi_agent_version": "v2",
                "supported_reasoning_levels": ["low", "ultra"],
            }
        ],
        "tool_spec_web_search_fields": {
            "external_web_access": {"type": "Option<bool>"},
            "indexed_web_access": {"type": "Option<bool>"},
        },
    }
    snapshot = {
        "schema_version": 1,
        "codex_source_commit": "same",
        "contract": complete_value_contracts,
    }

    classification = classify_snapshots(snapshot, snapshot)

    classified_paths = "\n".join(classification["high_confidence_unchanged"])
    for key in complete_value_contracts:
        assert f"contract.{key}" in classified_paths
    assert classification["possibly_unchanged"] == []
    assert classification["changed"] == []


def test_snapshot_classification_reports_commit_and_contract_changes():
    baseline = {
        "schema_version": 1,
        "codex_source_commit": "old",
        "contract": {"endpoints": {"RESPONSES_ENDPOINT": "/responses"}},
    }
    current = {
        "schema_version": 1,
        "codex_source_commit": "new",
        "contract": {"endpoints": {"RESPONSES_ENDPOINT": "/v2/responses"}},
    }

    classification = classify_snapshots(baseline, current, check_source_commit=False)

    assert classification["high_confidence_unchanged"] == []
    assert classification["possibly_unchanged"] == []
    assert classification["changed"] == [
        "codex_source_commit: old -> new (ignored; does not affect exit status)",
        "contract.endpoints: extracted value changed (see detailed diff)",
    ]
