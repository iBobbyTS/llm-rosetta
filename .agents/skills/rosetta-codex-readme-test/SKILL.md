---
name: rosetta-codex-readme-test
description: Run controlled Codex-through-Codex-Rosetta README editing tests in /Users/ibobby/Projects/AGENTS.md-test. Use when Codex needs to enable Rosetta backend logging, route a Codex CLI run through the gateway for a specific model, test whole-file README rewrite versus localized edit behavior, identify the resulting Codex session JSONL and Rosetta log, verify the real working-tree outcome, and then revert the test repository changes.
---

# Rosetta Codex README Test

## Purpose

Use this skill to run repeatable agent-behavior tests against
`/Users/ibobby/Projects/AGENTS.md-test` through `codex-rosetta-gateway`.
The test repository is disposable for content changes, but preserve diagnostics
and revert the repository after inspection.

## Defaults

- Choose the model by debugging goal:
  - To observe Codex/GPT's native request shape, use `gpt-5.6-terra`. The
    gateway trace must confirm the request actually reached the GPT route; a
    local alias that forwards this name to a third-party model does not count.
  - To debug third-party model conversion or agent behavior, use
    `deepseek-v4-flash` by default because it is the low-cost test model.
- Default real-provider smoke matrix: `deepseek-v4-flash` and
  `gpt-5.6-terra`. Keep third-party aliases as separate matrix entries and
  verify every upstream route in the Rosetta trace rather than assuming it
  from the Codex-facing model name.
- Temporary Rosetta gateway configs live under
  `/Users/ibobby/Projects/codex-rosetta/rosetta-test-config`.
- The isolated Codex home lives at
  `/Users/ibobby/Projects/codex-rosetta/codex-test-home`.
- Gateway trace logs and captured Codex stdout/stderr should be kept under
  `/Volumes/RAM Disk`.

## Workflow

1. Confirm the test repository exists:

   ```bash
   test -d /Users/ibobby/Projects/AGENTS.md-test
   ```

2. Confirm `codex-rosetta-gateway` is running and locate the active port. The
   usual local endpoint is `http://127.0.0.1:8765/v1`. When testing current
   uncommitted gateway code, prefer launching a separate gateway instance on a
   free port with a config copied into
   `/Users/ibobby/Projects/codex-rosetta/rosetta-test-config`, rather than
   changing or killing the user's main gateway.

   ```bash
   curl -sS http://127.0.0.1:8765/v1/models | python3 -m json.tool | sed -n '1,120p'
   ```

3. Enable Rosetta backend logging before starting the Codex run. Use the admin
   UI/API or the project's current config mechanism. Set:

   - log path: an explicit path under `/Volumes/RAM Disk`, for example
     `/Volumes/RAM Disk/<model>-readme-test-<timestamp>.jsonl`
   - model filter: the model being tested. Use `gpt-5.6-terra` for native GPT
     request observation and `deepseek-v4-flash` for third-party conversion
     debugging, unless the user explicitly specifies another model

   If the exact admin API is uncertain, inspect existing gateway config/routes
   before changing anything. Do not leave broad logging enabled after the test.

4. Run a single Codex CLI test with an isolated `CODEX_HOME` if the user has not
   specified another home. A known-good minimal config can live at
   `/Users/ibobby/Projects/codex-rosetta/codex-test-home/config.toml` and point
   to the Rosetta base URL:

   ```toml
   model_provider = "rosetta"
   model = "deepseek-v4-flash"
   sandbox_mode = "danger-full-access"
   approval_policy = "never"
   model_reasoning_effort = "medium"

   [model_providers.rosetta]
   name = "rosetta"
   wire_api = "responses"
   requires_openai_auth = true
   base_url = "http://127.0.0.1:8765/v1"
   experimental_bearer_token = "none"

   [projects."/Users/ibobby/Projects/AGENTS.md-test"]
   trust_level = "trusted"
   ```

5. Choose exactly one prompt according to the test goal:

   Whole-file rewrite:

   ```text
   帮我重新组织一下README.md里的语言（重写整个文件）
   ```

   Localized edit:

   ```text
   帮我重新组织一下README.md里的语言（使用局部编辑，不要整体重写）
   ```

   Run the prompt non-interactively, capture stdout/stderr, and keep the command
   bounded:

   ```bash
   MODEL=deepseek-v4-flash
   CODEX_HOME=/Users/ibobby/Projects/codex-rosetta/codex-test-home \
     codex exec --json --skip-git-repo-check \
     -C /Users/ibobby/Projects/AGENTS.md-test \
     -m "$MODEL" '<prompt>' \
     > "/Volumes/RAM Disk/codex-readme-test-${MODEL}.jsonl" \
     2> "/Volumes/RAM Disk/codex-readme-test-${MODEL}.stderr"
   ```

6. Immediately disable Rosetta backend logging after the Codex run finishes,
   even if the run failed. Keep the generated log file for inspection.

7. Confirm the result from three evidence sources:

   - Working tree: run `git -C /Users/ibobby/Projects/AGENTS.md-test status --short`
     and inspect the README diff with `git diff -- README.md`. Do not judge
     answer quality unless the user explicitly asks; focus on whether the file
     changed and how.
   - Codex session JSONL: extract `thread_id` from the `codex exec --json`
     stdout, then locate the rollout under
     `/Users/ibobby/Projects/codex-rosetta/codex-test-home/sessions`. Search
     filenames first and avoid reading the full file because early system
     prompts are large.
   - Rosetta log: inspect the configured log path with bounded JSONL tools,
     filtering by model, request id, session id, or timestamp. Do not dump full
     logs into the reply.

8. Revert the test repository changes after evidence capture:

   ```bash
   git -C /Users/ibobby/Projects/AGENTS.md-test restore README.md
   git -C /Users/ibobby/Projects/AGENTS.md-test status --short
   ```

   If files other than `README.md` changed, stop and report them before
   reverting unless the user explicitly asked for broader cleanup.

## Real Provider Smoke And EOF Regression

Use this workflow when validating whether real Chat upstream providers still
work through Rosetta's Responses downstream path, especially after stream
terminal-event, phase-buffer, or tool-adaptation changes. The goal is to verify
real Codex behavior and Rosetta stream shape; do not judge answer quality unless
the user asks.

1. Prefer an isolated gateway on a free port when testing uncommitted gateway
   code. Use a copied config under
   `/Users/ibobby/Projects/codex-rosetta/rosetta-test-config`, enable verbose
   stream trace logging to an explicit `/Volumes/RAM Disk/*.jsonl` path, and set
   the trace model filter to the tested upstream models, for example
   `deepseek-v4-flash,glm-5.2`.

2. Point the isolated Codex home at the test gateway. Back up
   `/Users/ibobby/Projects/codex-rosetta/codex-test-home/config.toml` before
   editing it, and restore it after the run.

3. For each model in the matrix, run at least these tests:

   Short final-answer test:

   ```text
   只回复一行：OK
   ```

   Read/tool test:

   ```text
   读取 README.md 的第一行，然后只回复这一行原文。
   ```

   Multi-turn modification test:

   ```text
   这是一个真实 provider 多轮次修改测试。请严格按顺序执行，且不要把多个步骤合并到同一个 shell 命令：1. 用一次独立的命令读取 README.md 的第一行。2. 收到结果后，用另一次独立的命令在 README.md 末尾追加一行：Rosetta multi-turn smoke: <model>。3. 收到结果后，用第三次独立命令检查 git diff -- README.md。4. 最终只回复一行：done:<model>
   ```

   Capture each run separately:

   ```bash
   MODEL=deepseek-v4-flash
   TS=$(date +%Y%m%d-%H%M%S)
   CODEX_HOME=/Users/ibobby/Projects/codex-rosetta/codex-test-home \
     codex exec --json --skip-git-repo-check \
     -C /Users/ibobby/Projects/AGENTS.md-test \
     -m "$MODEL" '<prompt>' \
     > "/Volumes/RAM Disk/codex-provider-smoke-${MODEL}-${TS}.jsonl" \
     2> "/Volumes/RAM Disk/codex-provider-smoke-${MODEL}-${TS}.stderr"
   ```

4. For modification tests, capture the README diff and then restore only the
   test file:

   ```bash
   git -C /Users/ibobby/Projects/AGENTS.md-test diff -- README.md
   git -C /Users/ibobby/Projects/AGENTS.md-test restore README.md
   git -C /Users/ibobby/Projects/AGENTS.md-test status --short
   ```

5. Inspect the Codex JSONL output without dumping full logs. Confirm:

   - exit status is `0`
   - short test final message is `OK`
   - read/tool test used command execution and returned the README first line
   - multi-turn test has separate command executions for read, write, and diff
   - final message is exactly `done:<model>`
   - any `error` item is distinguished from fatal failure; Codex may emit a
     non-fatal unknown-model metadata warning for custom model names

6. Inspect the Rosetta trace by request, not by raw line count. For each stream,
   confirm:

   - `stream_complete=true` and `stream_error=null`
   - downstream SSE includes exactly one `response.completed`
   - the corresponding source event includes exactly one `response.completed`
   - `ir_event` includes one `finish` and one `stream_end`
   - no duplicate completion is introduced by EOF fallback
   - DeepSeek-style streams usually end with `choices_len=1`,
     `finish_reason` and `usage`
   - GLM-style streams usually end with a prior `finish_reason` followed by an
     empty `choices: []` chunk carrying `usage`

7. Treat the EOF fallback as exercised only if the trace shows a structured
   `finish` without the provider's normal terminal chunk before upstream EOF.
   If DeepSeek or GLM complete through their normal terminal shapes, record that
   the fallback was not needed and that the real-provider test verified existing
   success paths were not broken.

8. Stop only the isolated gateway instance started for the test, restore the
   backed-up Codex home config, and leave the user's main gateway alone.

## Safety Rules

- Keep this workflow scoped to `/Users/ibobby/Projects/AGENTS.md-test`.
- Do not run it in the codex-rosetta repository or any production project.
- Do not leave backend logging enabled.
- Do not read whole Codex rollout JSONL files or whole Rosetta JSONL logs.
- Redact API keys, bearer tokens, cookies, and authorization headers from any
  report.
- Revert only the test repository changes produced by this run. Do not reset or
  clean unrelated user work.

## Final Report

Report:

- model, prompt variant, exit status, and final assistant message if relevant
- working-tree status before cleanup and after cleanup
- Codex thread/session id and exact rollout JSONL path
- Rosetta log path and whether the expected request or requests appeared
- for real-provider smoke tests: per-model short/read/multi-turn result,
  command-execution count or summary, upstream model observed in the trace, and
  terminal stream shape
- any warning or error that affects interpreting the test
