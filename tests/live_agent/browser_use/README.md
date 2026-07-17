# In-App Browser GUI Live Test

This suite exercises the bundled in-app Browser against a deterministic local
fixture. It separates Browser execution from evidence evaluation: one GUI
executor records only Browser calls and visible postconditions, then the user
copies that report to a new judge-agent session for rollout/Gateway correlation
and the final verdict. It is one standalone live scenario, `01`; it is not a
model-quality benchmark and it is not part of the automated pytest or Codex CLI
matrix.

## Mandatory execution boundary

All of these requirements are hard validity gates:

- Run the test in the **Codex GUI app** in one normal, user-visible main task.
- Invoke the explicitly attached `@Browser` plugin and
  `$browser:control-in-app-browser` skill.
- Select the in-app Browser with the skill-prescribed `iab` binding and perform
  browser actions only through that selected surface.
- Do not use `codex`, `codex exec`, another Codex CLI mode, or the repository's
  CLI-based `rosetta-codex-readme-test` runner.
- Do not spawn, message, or delegate to a subagent. The executor main task must
  execute and observe every test action itself.
- Do not select, attach, or fall back to Chrome. This includes the Browser
  runtime's Chrome extension backend and the separate Chrome plugin.
- Do not substitute standalone Playwright, Selenium, Computer Use, `web.run`,
  an external browser MCP server, or raw HTTP for browser interactions.

If the in-app Browser cannot be selected, the executor stops and records the
condition without classifying it. A later judge decides whether the result is
`invalid_environment`. A successful Chrome or subagent run is not evidence for
this suite.

The executor GUI task may use its ordinary shell/`exec_command` tool solely to
start and stop the deterministic localhost fixture server and write its
execution report. It must not collect or inspect Gateway logs, Gateway traces
or databases, Request Logs, session JSONL, or rollout JSONL. That evidence is
reserved for the independent judge session.

On a Responses-to-Chat route, the independent judge also verifies the Node
tool-adaptation path. After `tool_search` finds the live Browser execution tool,
the target Chat request must expose the exact matched Node REPL Function, the
model must send structured arguments to that Function, and Rosetta must return
the call to Codex as deterministic custom `exec`. This is judge-side wire
evidence only; the executor must not inspect or report it.

## Files

- `01/TASK.md`: exact prompt to paste into a fresh Codex GUI main task.
- `01/expected.json`: machine-readable execution gates and expected capability
  postconditions.
- `EXECUTION_REPORT.md`: non-judgmental report contract for the test executor.
- `JUDGE_TASK.md`: prompt for the separate judge-agent session.
- `EVALUATION.md`: judge-only evidence contract and result classification.
- `serve_fixture.py`: localhost-only fixture server.
- `fixture/`: deterministic HTML, iframe, image, and download resources.

## Per-run workspace

Every execution owns one immutable local-time run root:

```text
.agent-work/live-agent-test/{YYYYMMDD-HHMM}/
  execution.json
  evaluation.json
  fixture-server.log
```

The executor computes `{YYYYMMDD-HHMM}` once at the beginning of the run using
the host's local time and creates the exact directory atomically. It must not
use an existing directory, delete or clear an earlier run, choose the newest
directory, or write to a shared `artifacts/browser_use/01` path. If the exact
minute directory already exists, stop before Browser setup and ask the user to
start the test again in a new minute. Do not add model names, counters, seconds,
or other suffixes to the directory name.

The executor writes `execution.json` and bounded fixture-server files only
inside that run root. After handoff, the judge receives the exact run-root path,
reads that root's `execution.json`, and writes `evaluation.json` back to the
same root. The judge must never infer a run by selecting the latest timestamp.

## Prerequisites

1. Start the Codex GUI app and open this repository as the workspace.
2. Ensure the `Browser` plugin is installed and the
   `browser:control-in-app-browser` skill appears in the task's available
   skills.
3. Run the Gateway under test. Do not give its log destination to the executor;
   the independent judge locates bounded evidence after handoff.
4. Start with a fresh GUI main task. Explicitly attach both `@Browser` and the
   Browser skill when submitting the contents of `01/TASK.md`.
5. Do not start the task from a terminal and do not use a subagent to execute
   it. Evaluation happens later in a separate user-created judge session, not
   inside the executor task.

The task starts the fixture server on `127.0.0.1:8876` from the GUI task's
shell tool. If the port is already occupied, the main task must identify and
stop only a prior instance of this fixture or report the conflict; it must not
silently switch ports because the expected URLs are deterministic.

## Scenario

`01` covers the useful in-app Browser surface in one controlled session:

- binding and backend identity, tab lifecycle, navigation, reload, back, and
  forward;
- DOM snapshots, semantic locators, form controls, iframe interaction, popup
  claim, screenshots, console logs, downloads, and page assets;
- Playwright keyboard/pointer operations, DOM-CUA, coordinate CUA, scrolling,
  and drag postconditions;
- alert/confirm/prompt behavior, visibility, viewport, clipboard, CDP command
  and filtered event behavior, and stale-tab recovery;
- final tab and fixture-process cleanup.

The suite deliberately does not inspect browser history, cookies, storage,
profiles, passwords, or session stores. It also does not request camera,
microphone, location, financial, destructive, account, or external-message
actions.

## Execute and hand off

Paste `01/TASK.md` into a fresh Codex GUI main task with the two required
Browser attachments. The executor reads `EXECUTION_REPORT.md`, creates
`<run_root>/execution.json`, and records observations without assigning
statuses or an overall classification. It must not read
`EVALUATION.md`, Gateway Logs, Gateway traces/databases, Request Logs, session
JSONL, or rollout JSONL.

This suite is explicitly **not fail-fast**. A call error, missing postcondition,
unsupported operation, or policy skip must be recorded without assigning a
status, followed by bounded recovery on the same IAB binding and the next
capability group. Reload the fixture for ordinary state contamination; if the
tab is stale, blocked, or unusable, discard only that tab binding and create a
fresh fixture tab from the existing IAB binding. A complete run must contain
one observation row for every executor capability group in `expected.json`.
Early termination is allowed only when an execution gate is invalid or the IAB
browser binding itself is unavailable/disconnected and cannot continue.

After cleanup, the executor tells the user to copy its complete final response,
the exact run-root path, the `execution.json` path, and the source session/thread
id into a new judge session. The judge follows `JUDGE_TASK.md` and
`EVALUATION.md`, inspects bounded Gateway/session evidence, and writes the
separate `evaluation.json` into that same run root. The executor must never
judge its own run.

The historical 2026-07-16 exploratory run is retained in
`.agent-work/browser-live-test-results.md`. It is useful as a baseline but does
not replace a fresh run of this suite.
