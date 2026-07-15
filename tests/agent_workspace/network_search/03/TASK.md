Use only the available `web.run` network tool and perform these operations in
order. Each numbered operation must be a separate tool call.

1. Call `open` on the direct URL `https://example.com/`. Require a non-error
   result containing `Example Domain`, exactly one numbered link with id `1`,
   and a new page reference matching `turnXfetchY`. Retain that exact reference.
2. Call `find` on that exact page reference with pattern `Example Domain`.
   Require at least one matching line.
3. Call `click` on the same page reference with link id `1`. Require a non-error
   result containing a new `turnXfetchY` page reference.

Do not use search first. Do not use shell commands, direct HTTP requests,
external browser tools, local file inspection, or prior knowledge as
substitutes. Do not combine operations into one call.

If all three calls succeed with the required references, reply with only
`RESULT:WEB_RUN_BROWSER_NAVIGATION_OK`. Otherwise reply with only
`RESULT:WEB_RUN_BROWSER_NAVIGATION_FAILED`.
