Use only the available `web.run` network tool. Make exactly one tool call whose
`time` array contains exactly these two entries in this order:

1. `{"utc_offset": "+03:00"}`
2. `{"utc_offset": "-05:30"}`

Require a non-error result containing one ISO 8601 timestamp labelled
`+03:00` and one ISO 8601 timestamp labelled `-05:30`. Do not call
`search_query`, `open`, or any other operation. Do not use shell commands,
direct HTTP requests, external browser tools, local time tools, or prior
knowledge as substitutes.

If the single call returns both requested fixed-offset times, reply with only
`RESULT:WEB_RUN_TIME_OK`. Otherwise reply with only
`RESULT:WEB_RUN_TIME_FAILED`.
