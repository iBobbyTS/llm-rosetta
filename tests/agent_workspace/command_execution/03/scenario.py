"""Request one input value from an existing process session."""

import sys

print("INPUT:VALUE", flush=True)
value = sys.stdin.readline().rstrip("\r\n")
if value == "rosetta":
    print("RESULT:INPUT_OK", flush=True)
else:
    print(f"RESULT:INPUT_BAD:{value!r}", flush=True)
