Test the top-level Code Mode `wait` Function.

1. Call `exec` once with exactly this raw JavaScript source, not a JSON object:

```javascript
text("WAIT_PHASE_1");
yield_control();
await new Promise((resolve) => setTimeout(resolve, 1500));
text("WAIT_PHASE_2");
```

2. The first result should report `Script running with cell ID ...`. Call
   `wait` on that exact cell id with `yield_time_ms` set to `5000`. If it yields
   again, call `wait` again on the same cell id until it completes.
3. Do not call `write_stdin`, start a shell command, or start a second `exec`
   cell.

If the `wait` result completes successfully and contains `WAIT_PHASE_2`, reply
with only `RESULT:CODE_MODE_WAIT_OK`. Otherwise reply with only
`RESULT:CODE_MODE_WAIT_FAILED`.

