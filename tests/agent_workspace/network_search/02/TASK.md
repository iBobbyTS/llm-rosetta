Use only the available `web.run` network tool and perform these operations in
order. Each numbered operation must be a separate tool call.

1. Call `search_query` for the official Python 3 documentation. Select a
   returned `docs.python.org` reference such as `turn0search0`.
2. Call `open` on that exact stored search reference. Require a non-error result
   containing readable Python documentation content.
3. Call `find` on the same reference with pattern `Library Reference`. Rosetta
   currently does not implement this operation; require an explicit Not
   Implemented error that mentions `commands.find` and
   `Consider "Browser Use" skill`.
4. Even after that expected error, call `click` on the same reference with link
   id `0`. Require an explicit Not Implemented error that mentions
   `commands.click` and `Consider "Browser Use" skill`.

Do not use shell commands, browser automation, local file inspection, direct
HTTP requests, or prior knowledge as substitutes. Do not combine `find` or
`click` with another operation in one call.

If search and open succeed and both unsupported operations return exactly the
expected class of error, reply with only `RESULT:WEB_RUN_NAVIGATION_OK`.
Otherwise reply with only `RESULT:WEB_RUN_NAVIGATION_FAILED`.

