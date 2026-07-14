Test the complete localized file-tool workflow. Perform these operations in
order using the exact named tools:

1. Call `Glob` with pattern `fixtures/*.txt`. Require both
   `fixtures/alpha.txt` and `fixtures/beta.txt` in the result.
2. Call `Grep` for `ROSETTA_EDIT_TARGET` under `fixtures` with
   `output_mode` set to `content`. Require a match in `fixtures/alpha.txt`.
3. Call `Read` on `fixtures/alpha.txt`. Require the complete line
   `status=original`.
4. Call projected `apply_patch` with this exact patch:

```text
*** Begin Patch
*** Update File: fixtures/beta.txt
@@
-status=unchanged
+status=patched
*** End Patch
```

5. Call `Edit` on `fixtures/alpha.txt`, replacing the exact complete line
   `status=original` with `status=edited`. Do not use `replace_all`.
6. Call `Write` to create `fixtures/created.txt` with exact content
   `CREATED_BY_WRITE` followed by one newline.

Do not use shell commands, Python, or any other file tools as substitutes.
Attempt all six named tools in order; if one fails,
do not repair the workspace through a fallback.

If all six calls succeed, reply with only `RESULT:LOCALIZED_FILES_OK`.
Otherwise reply with only `RESULT:LOCALIZED_FILES_FAILED`.
