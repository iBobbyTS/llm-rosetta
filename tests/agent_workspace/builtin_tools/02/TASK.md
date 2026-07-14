Test projected `update_plan` in Default mode. Do not switch to Plan mode.

1. Call `update_plan` with explanation `Initial tool test` and exactly these
   ordered steps:
   - `Inspect fixture` with status `in_progress`
   - `Report result` with status `pending`
2. After that call succeeds, call `update_plan` again with explanation
   `Tool test complete` and the same ordered steps, both with status
   `completed`.
3. Do not use a prose checklist or any other tool as a substitute.

If both tool calls succeed, reply with only `RESULT:UPDATE_PLAN_OK`. Otherwise
reply with only `RESULT:UPDATE_PLAN_FAILED`.

