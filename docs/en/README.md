# Codex-Rosetta User Documentation

## Compatibility

- [Codex version compatibility](version-compatibility.md)

## Gateway operations

- [Security and authentication](gateway-security.md)

The terminal supports three logging levels:

```bash
codex-rosetta-gateway --log-level info
codex-rosetta-gateway --log-level warning
codex-rosetta-gateway --log-level error
```

`info` is the default and prints request summaries. `warning` suppresses normal
per-request output while retaining warnings and errors; `error` prints errors
only. For complete request history, use **Request Log** in the WebUI. For
streaming trace diagnostics, use **Gateway Logs** in the WebUI.

## Codex tool localization

- [Basic conversation](codex-tool-localization/basic-conversation.md)
- [Code editing](codex-tool-localization/code-edit.md)
- [Other tools](codex-tool-localization/other-tools.md)

For architecture notes, source contracts, and maintenance procedures, see the
[developer documentation](../dev/README.md).
