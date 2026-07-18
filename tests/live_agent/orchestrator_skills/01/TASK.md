Perform these operations in order using only the named `skills` Namespace
tools:

1. Call `skills.list` with authority `{ "kind": "orchestrator" }`.
2. Find the returned entry whose `name` and `package` are both
   `orchestrator-skill-fixture`.
3. Call `skills.read` with the same orchestrator authority, that exact returned
   package, and its exact returned `main_resource` as `resource`.
4. Require the read result to contain `ORCHESTRATOR_SKILL_BODY_OK`.

Do not use local Skill discovery, shell, files, browser, network, MCP, plugin,
or app tools as substitutes. Do not modify resources.

If every step succeeds, reply with only `RESULT:ORCHESTRATOR_SKILLS_OK`.
Otherwise reply with only `RESULT:ORCHESTRATOR_SKILLS_FAILED`.
