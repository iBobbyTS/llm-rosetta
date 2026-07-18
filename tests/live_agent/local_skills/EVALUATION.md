# Local Filesystem Skill Evaluation

This file guides the outer evaluator. Do not include it in the tested model's
prompt.

## Required evidence

Use `artifacts/codex.jsonl`, the matching isolated rollout, and bounded Gateway
Logs. Prove the runner is `codex_exec_local` and the worktree contains the
copied fixture. In the rollout/request inputs, record only bounded structural
evidence for:

- one enabled catalog entry named `local-skill-fixture` with a filesystem
  locator ending in `.agents/skills/local-skill-fixture/SKILL.md`;
- one explicit Skill injection whose name and path match that catalog entry and
  whose body contains `RESULT:LOCAL_SKILL_OK`;
- no native or model-facing `skills.list`/`skills.read`, command, or file-tool
  calls;
- the exact final line `RESULT:LOCAL_SKILL_OK` and a completed stream.

The catalog entry alone does not prove the Skill body was loaded. Conversely,
a marker copied into the prompt would invalidate the fixture; confirm that the
isolated `TASK.md` does not contain the marker.

Write `artifacts/evaluation.json` with this shape:

```json
{
  "classification": "success | success with deviations | failure",
  "task_id": "01",
  "model": "Codex-facing model alias",
  "provider_identity": "codex_rosetta",
  "provider_display_name": "OpenAI",
  "upstream_model": "model proven by Gateway Logs",
  "thread_id": "Codex thread id",
  "rollout_path": "isolated rollout path",
  "runner": "codex_exec_local",
  "local_execution_environment_attached": true,
  "catalog_entry_observed": true,
  "catalog_source_kind": "filesystem",
  "explicit_skill_injection_observed": true,
  "injected_skill_name": "local-skill-fixture",
  "injected_skill_path_suffix": ".agents/skills/local-skill-fixture/SKILL.md",
  "injected_body_marker_observed": true,
  "skills_namespace_calls": 0,
  "prohibited_fallback_calls": 0,
  "success_marker_observed": true,
  "gateway_log_evidence": [],
  "stream_completed": true,
  "warning": null
}
```

Keep all evidence credential-free and bounded. Apply the repository-wide
per-request cache-continuation analysis from the runner skill.
