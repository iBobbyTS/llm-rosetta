Use only the available `web.run` network tool and perform these operations in
order. Each numbered operation must be a separate tool call.

1. Call `open` on the direct URL
   `https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf`.
   Require a non-error PDF result containing `Dummy PDF file`, a one-page count,
   and a new PDF reference matching `turnXviewY`. Retain that exact reference.
2. Call `find` on that exact PDF reference with pattern `Dummy PDF file`.
   Require at least one matching line.
3. Call `screenshot` on the same PDF reference with `pageno` set to `0`.
   Require a non-error result containing rendered dimensions, a text-source
   label, and `Dummy PDF file`.

Do not use shell commands, direct HTTP requests, external browser tools, local
file inspection, or prior knowledge as substitutes. Do not combine operations
into one call.

If all three calls succeed with the required PDF reference and rendered-page
text, reply with only `RESULT:WEB_RUN_PDF_OK`. Otherwise reply with only
`RESULT:WEB_RUN_PDF_FAILED`.
