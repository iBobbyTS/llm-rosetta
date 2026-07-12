# Code Editing

Codex exposes native editing capabilities such as `apply_patch`, `exec_command`, and `write_stdin`. Many open models have seen more Claude Code-style editing tools during training or product use, so they may choose shell commands or ad-hoc Python scripts instead of Codex's patch workflow.

Codex-Rosetta can localize the model-facing editing surface for Responses-to-Chat routes while still returning Codex-native tool calls downstream.

## Model Configuration

The gateway admin UI exposes a model-level section named `Tool Adaption for Codex`.

Current options:

- `Localize code editing tools`: replace Codex-native editing tools with localized Chat tools for the upstream model.
- Tool Profiles manage the current `image_gen.imagegen` namespace tool. The obsolete hosted `image_generation` tool is not part of the bundled Profile catalog.
- `Tool call mapping cache TTL`: how long persisted localized/native tool-call mappings remain valid.

Only the configured model route is affected.

## Model-Facing Tools

When `localize_code_editing_tools` is enabled for an OpenAI Responses to OpenAI Chat route, Rosetta removes native code editing tools from the upstream Chat request and exposes these Claude Code-like tools instead:

- `Read(file_path, offset?, limit?)`
- `Edit(file_path, old_string, new_string, replace_all?)`
- `Write(file_path, content)`
- `Glob(pattern, path?)`
- `Grep(pattern, path?, glob?, type?, output_mode?, case_insensitive?, line_numbers?, before_context?, after_context?, context?, head_limit?, offset?, multiline?)`
- `Bash(command, timeout?, description?, run_in_background?)`

The localized `Edit` description explicitly asks the model to replace complete lines or complete consecutive line blocks when possible. This improves conversion to Codex patches because `apply_patch` is much more reliable when the old text includes full line context.

## Native Translation

Localized tool calls are translated back before Codex receives the response:

- `Bash` becomes `exec_command`.
- `Read` becomes an `exec_command` that prints UTF-8 file contents, with optional offset and limit.
- `Glob` becomes an `exec_command` using Python `glob`.
- `Grep` becomes an `exec_command` using `rg`.
- `Write` normally becomes a custom `apply_patch` add-file call.
- `Edit` normally becomes a custom `apply_patch` call.
- `Edit(replace_all=true)` becomes an `exec_command` that performs a controlled replace-all operation.

If the original request does not expose custom `apply_patch`, `Edit` falls back to an `exec_command` or `shell_command` that invokes `apply_patch` through a heredoc when available, and `Write` falls back to an `exec_command` that writes UTF-8 content through a base64-safe Python helper.

## Read Output Expansion

Some models emit narrow substring edits even after reading the file. Rosetta maintains a session-local read-output cache while rebuilding the converted Chat request. When a later `Edit` targets a substring that can be expanded unambiguously to a full line from a prior `Read`, Rosetta expands `old_string` and `new_string` to full-line replacements before generating the patch.

The cache is invalidated for a file after successful mutating calls for that file, so stale reads are not reused across edits.

## Historical Tool-Call Mapping

Codex stores assistant tool calls in its local session history and sends that history again on later turns. After localization, Codex sees native calls such as `apply_patch`, but the upstream Chat model originally saw localized calls such as `Edit`.

To keep provider-side prompt caching and model continuity intact, Rosetta stores a mapping:

- `session_id`
- original localized tool call
- Codex-native tool call
- expiration time

For authenticated, window-scoped gateway requests, SQLite is the cross-request
source of truth; the mapping is not retained as an in-memory cross-turn cache.
The exact reversible payload is protected with AES-256-GCM at rest. Diagnostic
redaction is deliberately not applied to this executable payload, because a
`[REDACTED]` call would no longer describe the tool action Codex executed. See
[Gateway Security and Authentication](../gateway-security.md#executable-tool-history-storage)
for key lifecycle, backup, failure, and legacy-row behavior.

On later requests for the same session, Rosetta walks the historical messages and replaces known Codex-native calls with the original localized calls before sending the request upstream. If a loaded mapping is not used by the current outgoing request, Rosetta deletes it after the request is sent. Expired rows are cleaned periodically.

This keeps Codex's downstream history native while keeping the upstream model's repeated context stable.

## Current Limits

The localization layer is intentionally conservative:

- It only runs for Responses-to-Chat routes.
- It only changes routes where the model config enables it.
- It does not attempt to parse arbitrary shell edits back into structured edits.
- It cannot hide reasoning that the model places in ordinary text.
