# Clock And Memory Namespace Test

This suite verifies direct use of the locally available Codex `clock` and
`memories` Namespace surfaces through Codex-Rosetta. It does not test either
filesystem-backed Skills or orchestrator-owned Skills, and it does not measure
planning, research, coding, prose, or general agent quality.

## Scenario

- `01`: call one deterministic read-only operation from `clock` and `memories`.

Both Namespace checks live in one short task so a normal two-model matrix needs
only one isolated run for the default `gpt-5.6-sol` cell and one for
`deepseek-v4-flash`. Evaluation remains per Namespace: one missing or failed
Namespace does not erase evidence collected for the other.

Subagent lifecycle behavior is intentionally isolated in the
[`subagent_tools`](../subagent_tools/README.md) suite so each `collaboration`
Function can be evaluated independently.

## Required Codex runner and configuration

Run this suite through ordinary local `codex exec`. The attached local
execution environment is valid for Clock and Memories and is intentionally not
used as evidence about orchestrator-owned Skills.

Use the local-mode Provider ID `codex_rosetta` with provider display name
exactly `OpenAI` for both models. Do not define any suite-specific Provider.
Keep the isolated Rosetta localhost `base_url` and retain the catalog generated
by local mode.

Add these feature flags to the isolated `config.toml`:

```toml
[features]
current_time_reminder = true
memories = true

[memories]
generate_memories = false
use_memories = true
dedicated_tools = true
```

After copying the suite task, prepare the isolated memory store without reading
or modifying the user's real memories:

```bash
mkdir -p "$RUN_ROOT/codex_home/memories"
cp "$RUN_ROOT/worktree/memory_fixture.md" \
  "$RUN_ROOT/codex_home/memories/memory_summary.md"
```

Filesystem-backed Skill discovery is covered by
[`local_skills`](../local_skills/README.md). The distinct `skills.list` and
`skills.read` Namespace surface is covered by
[`orchestrator_skills`](../orchestrator_skills/README.md), which requires an
app-server runner without an attached local execution environment. Do not infer
either Skill surface from this suite.

## Provider matrix

Run task `01` once with each model:

| Model | Gateway model group | Expected route |
|---|---|---|
| `gpt-5.6-sol` (default GPT) | `GPT中转站` | direct OpenAI Responses |
| `deepseek-v4-flash` (default third-party text) | `DeepSeek` | Responses-to-Chat conversion |

Use a separate timestamp run root, Codex home, copied Gateway config, port, and
Gateway Logs trace for each row. Confirm the actual upstream model from Gateway
Logs rather than trusting the Codex-facing alias.

## Result interpretation

Follow [`EVALUATION.md`](EVALUATION.md) and write
`artifacts/evaluation.json`. A Namespace succeeds only when the rollout shows
the native call and a non-error result. Gateway Logs must additionally prove
the model-facing call name and conversion route.

For the Chat route, expected model-facing names are the unique flattened forms
`clock.curr_time` and `memories.list`; Rosetta must restore their native
Namespace metadata before Codex executes them. The exact name may differ only
if the current Codex/Rosetta contract deliberately changes the stable
flattening scheme, in which case record the observed name and review the
compatibility ledger.
