"""Deterministic command used to force a second Codex model turn."""

import hashlib

print("COMMAND:COMPACTION_PROTOCOL_READY")
print(
    "FILLER:"
    + "".join(
        hashlib.sha256(f"fixture-{index}".encode()).hexdigest() for index in range(2000)
    )
)
