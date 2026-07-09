"""Tests for the Codex source compatibility contract extractor."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "version-compatibility" / "codex-source-contract.json"
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_codex_compatibility.py"
SCRIPT = runpy.run_path(str(SCRIPT_PATH))

_enum_variants = SCRIPT["_enum_variants"]
_serde_enum_wire_types = SCRIPT["_serde_enum_wire_types"]
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
    assert "高置信度没有变化的：" in rendered
    assert "可能没有变化的：" in rendered
    assert "有变化的：\n  - 无" in rendered


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
        "codex_source_commit: old -> new（已忽略，不影响退出码）",
        "contract.endpoints: 已提取值发生变化（见详细 diff）",
    ]
