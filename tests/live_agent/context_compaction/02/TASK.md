Run `python3 scenario.py` to create the deterministic large command result for
this protocol test. Do not read the script to infer its output and do not run
any other command. Configure both the outer Code Mode `exec` cell and the
nested command tool to retain at least 20,000 output tokens (use
`// @exec: {"max_output_tokens": 20000}` and nested
`max_output_tokens=20000` when available); assign the nested result and emit it
with `text(JSON.stringify(result))` so the outer cell retains the full payload.
A lower outer or nested result cap, or dropping the result before emitting it,
is invalid because it cannot retain the required compaction payload.
After at least one completed command result and the automatic compaction/replay,
reply with only `RESULT:COMPACTION_PROTOCOL_OK`.
