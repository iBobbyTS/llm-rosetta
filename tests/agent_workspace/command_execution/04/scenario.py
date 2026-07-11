"""Request two ordered inputs through one persistent process session."""

import sys

print("INPUT:FIRST", flush=True)
first = sys.stdin.readline().rstrip("\r\n")
print("INPUT:SECOND", flush=True)
second = sys.stdin.readline().rstrip("\r\n")
if (first, second) == ("alpha", "beta"):
    print("RESULT:TWO_STAGE_OK", flush=True)
else:
    print(f"RESULT:TWO_STAGE_BAD:{first!r}:{second!r}", flush=True)
