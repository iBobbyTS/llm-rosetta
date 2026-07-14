Test one complete Goal lifecycle in this fresh thread.

1. Call `get_goal` with no arguments and confirm there is no active goal.
2. Call `create_goal` with objective `Verify projected Goal tools`. Do not set
   `token_budget`.
3. Call `get_goal` again and require that exact objective to be active.
4. Call `update_goal` with status `complete`.

Do not use prose as a substitute for any Goal call. If all four calls succeed,
reply with only `RESULT:GOAL_LIFECYCLE_OK`. Otherwise reply with only
`RESULT:GOAL_LIFECYCLE_FAILED`.

