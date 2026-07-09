---
name: inspect-rosetta-sessions
description: Inspect Codex-Rosetta gateway JSONL logs and Codex rollout session JSONL files without reading huge prompts or logs into context. Use when Codex needs to locate a Codex session by id, summarize a rollout session, analyze Rosetta request/stream logs, compare direct vs gateway behavior, inspect tool definitions/tool calls/reasoning/phase fields, or diagnose Codex agent-loop and tool-adaptation issues.
---

# Inspect Rosetta Sessions

## Overview

Use this skill for read-only investigation of Codex-Rosetta gateway behavior against Codex sessions. Prefer the bundled script for JSONL inspection so large system prompts, full contexts, and verbose gateway logs are streamed and summarized instead of dumped into the model context.

## Local Test Paths

For Rosetta/Codex test runs created by `rosetta-codex-readme-test`, prefer these
paths:

- Codex test home:
  `/Users/ibobby/Projects/codex-rosetta/codex-test-home`
- Codex test sessions:
  `/Users/ibobby/Projects/codex-rosetta/codex-test-home/sessions`
- Rosetta temporary configs:
  `/Users/ibobby/Projects/codex-rosetta/rosetta-test-config`
- Gateway trace logs and captured test stdout/stderr:
  `/Volumes/RAM Disk`
- Model selection depends on the evidence needed: use `gpt-5.6-terra` when
  observing Codex/GPT's native request shape, and use `deepseek-v4-flash` for
  low-cost third-party conversion debugging. Confirm the actual upstream model
  in the Rosetta log; the Codex-facing alias alone is not evidence.

## Workflow

1. Resolve session ids before opening files. For Rosetta README tests, search
   filenames first under
   `/Users/ibobby/Projects/codex-rosetta/codex-test-home/sessions`. For normal
   user sessions, search `~/.codex/sessions` and `~/.codex/archived_sessions`.
   Only scan file contents when a filename search fails.
2. Summarize sessions with the script. Do not `cat` rollout files, because early system-prompt lines can be very large.
3. Summarize gateway logs from `/Volumes/RAM Disk` with a `--session-id` filter
   when possible. Logs can include full request/response bodies and temporary
   credentials; keep output redacted and bounded.
4. When comparing direct vs Rosetta, summarize both sessions and the Rosetta log, then compare structure rather than answer quality: models, request counts, tool definitions, tool calls, output item types, `phase`, `reasoning`, `reasoning_content`, warnings, and errors.
5. If a finding depends on an exact raw line, reopen only that line range with a short Python one-liner or extend the script output. Avoid full-file reads.

## Helper Script

Run from the repository root:

```bash
python .agents/skills/inspect-rosetta-sessions/scripts/inspect_rosetta_sessions.py find-session --root /Users/ibobby/Projects/codex-rosetta/codex-test-home/sessions 019f3cec-c207-7013-9c18-6e7af1369e09
python .agents/skills/inspect-rosetta-sessions/scripts/inspect_rosetta_sessions.py session-summary 019f3cec-c207-7013-9c18-6e7af1369e09
python .agents/skills/inspect-rosetta-sessions/scripts/inspect_rosetta_sessions.py log-summary "/Volumes/RAM Disk/log.jsonl" --session-id 019f3cec-c207-7013-9c18-6e7af1369e09
```

Useful options:

- `find-session --content`: stream-scan file contents if filename lookup fails.
- `session-summary --show-text`: include bounded message/text snippets.
- `log-summary --tail 5000`: inspect only the final lines of a large log.
- `--max-samples N`: cap printed tool/message/error samples.
- `--max-text-chars N`: cap each snippet.

## Investigation Checklist

For Codex/Rosetta gateway issues, explicitly check:

- Session identity: confirm the rollout id, session path, and whether the file is in `sessions` or `archived_sessions`.
- Model routing: list models in order and look for unexpected bootstrap requests or model switches.
- Request shape: compare `previous_response_id`, message/input counts, tool counts, and whether the request is direct `responses` pass-through or converted through `chat`.
- Tool surface: compare exposed tool names and emitted tool calls. For Codex editing localization, check whether model-facing tools differ from native Codex tools and whether returned calls are executable by Codex.
- Reasoning display: check `phase`, `reasoning`, encrypted reasoning items, and `reasoning_content` preservation. Missing `phase` often explains non-folded work output.
- Stream shape: compare `response.created`, output item creation/delta/done events, final completion events, and warnings/errors.
- Gateway anomalies: inspect conversion warnings, orphaned tool-call repairs, upstream error bodies, and full-log stage names.

## Safety Rules

- Treat all inspections as read-only unless the user separately asks for code changes.
- Do not dump full gateway logs or rollout files into the reply.
- Redact API keys, bearer tokens, authorization headers, cookies, and configured provider secrets.
- Do not modify gateway databases while investigating logs.
