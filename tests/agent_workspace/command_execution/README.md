# Command Execution Suite

This suite isolates command start and continuation behavior. It intentionally
avoids file-editing tasks and broad repository questions so that failures can
be attributed to tool selection, argument construction, or process-session
handling.

## Task matrix

| Task | Behavior under test | Expected native pattern |
|---|---|---|
| `01` | One short foreground command | One command start; no continuation |
| `02` | A command that outlives the initial yield | One command start; one or more empty-input polls on the returned session |
| `03` | One interactive prompt | One command start; one non-empty write to the returned session |
| `04` | Two interactive stages | One command start; two ordered non-empty writes to the same session |

Each numbered directory contains:

- `TASK.md`: the prompt passed verbatim to `codex exec`;
- `scenario.py`: the deterministic local process;
- `expected.json`: machine-readable expectations for evidence review.

`common/AGENTS.md` is copied into every runtime workspace. The prompts specify
the required wait/interaction pattern directly so the test does not depend on
planning skill or repository knowledge.

## Result interpretation

A task passes only when both conditions hold:

1. the final assistant message contains exactly the expected `RESULT:` marker;
2. the Codex rollout shows the interaction pattern in `expected.json`.

Do not treat a correct final marker as sufficient if the agent restarted a
process instead of continuing its session. Do not score wording, explanation,
or efficiency beyond the explicit call-count constraints.
