"""Emit delayed output so the agent must continue an existing session."""

import time

print("STATE:STARTED", flush=True)
time.sleep(4)
print("RESULT:DELAYED_OK", flush=True)
