# Orchestrator Skills Evaluation

This file guides the outer evaluator. Do not include it in the tested model's
prompt.

Before evaluating model behavior, prove the runner used Codex app-server,
`[orchestrator.skills]` was enabled, no local execution environment was
attached, and the deterministic orchestrator provider was provisioned. If any
precondition is unproven, use `runner_not_supported`.

For a supported run, require this exact native sequence:

1. one successful `skills.list` call with
   `{ "authority": { "kind": "orchestrator" } }`;
2. a returned enabled entry whose package and name are both
   `orchestrator-skill-fixture`;
3. one successful `skills.read` call using that exact authority, returned
   package, and returned `main_resource` as `resource`;
4. a read result containing `ORCHESTRATOR_SKILL_BODY_OK`;
5. the exact parent marker `RESULT:ORCHESTRATOR_SKILLS_OK`.

An empty list is a fixture/provider failure, not a successful Skills test.
Local Skill catalog entries, direct file reads, shell, or substitute MCP calls
do not count.

Write `artifacts/evaluation.json` with this shape:

```json
{
  "classification": "success | success with deviations | failure | runner_not_supported",
  "task_id": "01",
  "model": "Codex-facing model alias",
  "provider_identity": "codex_rosetta",
  "provider_display_name": "OpenAI",
  "upstream_model": "model proven by Gateway Logs",
  "thread_id": "Codex thread id",
  "rollout_path": "isolated rollout path",
  "runner": "app_server_orchestrator",
  "local_execution_environment_attached": false,
  "orchestrator_skills_enabled": true,
  "orchestrator_provider_provisioned": true,
  "skills_list_status": "success | not_exposed | not_called | failed",
  "fixture_package_observed": true,
  "skills_read_status": "success | not_exposed | not_called | failed",
  "read_used_returned_handles": true,
  "read_body_marker_observed": true,
  "prohibited_fallback_calls": 0,
  "success_marker_observed": true,
  "gateway_log_evidence": [],
  "stream_completed": true,
  "warning": null
}
```

Keep evidence bounded and credential-free. Apply the repository-wide
per-request cache-continuation analysis from the runner skill.
