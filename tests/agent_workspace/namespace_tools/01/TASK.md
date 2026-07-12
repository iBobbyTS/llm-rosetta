Perform these operations in order using only the named Namespace tools:

1. Call `clock.curr_time` with no arguments and require a non-error result.
2. Call `memories.list` with exactly `{}` (omit `path`) and require a non-error
   result that contains `memory_summary.md`.
3. Call `skills.list` with authority `{ "kind": "orchestrator" }` and require a
   non-error result. An empty skills list is acceptable.
4. Call `collaboration.spawn_agent` with task name `namespace_probe`, no forked
   turns, and the message `Reply with only SUBAGENT:NAMESPACE_OK without using
   tools.` Then use `collaboration.wait_agent` until that child finishes and
   require its exact marker `SUBAGENT:NAMESPACE_OK`.

Attempt all four numbered operations even if an earlier tool is unavailable or
returns an error. Do not stop early; this test needs separate evidence for each
Namespace.

Do not use shell commands, browser tools, local file tools, network search,
ordinary local Skill discovery, or prior knowledge as substitutes. Do not
modify files or memories.

If every required Namespace call succeeds, reply with only
`RESULT:NAMESPACE_TOOLS_OK`. If any tool is unavailable, not called, or returns
an error, reply with only `RESULT:NAMESPACE_TOOLS_FAILED`.
