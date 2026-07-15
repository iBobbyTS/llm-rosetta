Use only the available `web.run` network tool and perform these operations in
order. Each numbered operation must be a separate tool call.

1. Call `search_query` for the official Python 3 documentation. Set `domains`
   to exactly `["docs.python.org"]` and set the call's `response_length` to
   `"short"`. Require a non-error result containing a `docs.python.org` URL and
   retain its exact `turnXsearchY` reference.
2. Call `open` on that exact stored search reference with `lineno` set to `1`.
   Require readable Python documentation content whose returned line window
   starts at line 1 and does not contain an `L0:` line.

Do not use shell commands, browser automation, local file inspection, direct
HTTP requests, or prior knowledge as substitutes. Do not open the URL directly
in step 2; the stored search reference is required.

If both calls succeed with the required search options, reference, and line
window, reply with only `RESULT:WEB_RUN_REFERENCE_OPEN_OK`. Otherwise reply
with only `RESULT:WEB_RUN_REFERENCE_OPEN_FAILED`.
