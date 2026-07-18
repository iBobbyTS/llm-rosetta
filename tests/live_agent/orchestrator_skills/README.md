# Orchestrator Skills Namespace Test

This suite verifies the distinct orchestrator-owned Skill resource path:
`skills.list` discovers an opaque package and `skills.read` reads its opaque
main resource. It does not test local `.agents/skills` discovery or explicit
filesystem Skill injection.

## Required app-server runner

This suite requires Codex app-server. Do not run it through ordinary local
`codex exec`: current Codex disables orchestrator-owned Skills when the thread
has a local execution environment, and `codex exec` attaches one.

The app-server runner must:

- create the thread without a local execution environment;
- enable `[orchestrator.skills]`;
- route model requests through the isolated local-mode Gateway using Provider
  ID `codex_rosetta` and display name exactly `OpenAI`;
- inherit [`../runtime-contract.json`](../runtime-contract.json), including
  ChatGPT OAuth plus the Provider bearer and the two fixed secret-source
  locations, while keeping all copied credentials out of Git history;
- provision an enabled orchestrator Skill provider through the Codex Apps MCP
  resource surface with the deterministic fixture contract below.

| Field | Required value |
|---|---|
| package | `orchestrator-skill-fixture` |
| name | `orchestrator-skill-fixture` |
| main resource | opaque provider-owned resource returned by `skills.list` |
| main resource contents | contains `ORCHESTRATOR_SKILL_BODY_OK` |

If the runner cannot establish all four conditions, classify the cell as
`runner_not_supported`; do not attribute missing `skills.list`/`skills.read` to
the tested model or Rosetta. App-server alone is insufficient unless its
orchestrator Skill provider is also present.

## Scenario

- `01`: call `skills.list` with orchestrator authority, select the fixture,
  then call `skills.read` with the exact returned package and main resource.

Use `gpt-5.6-sol` and `deepseek-v4-flash` as the default text cells. Follow
[`EVALUATION.md`](EVALUATION.md) and write
`artifacts/evaluation.json`.
