Start `python3 scenario.py` once in a mode that allows later interaction,
arranging for the first command result to return within 1000 milliseconds.
When it prints `INPUT:FIRST`, continue that same process session and send
`alpha` followed by a newline. Wait for `INPUT:SECOND`, then continue the same
session again and send `beta` followed by a newline. Do not combine the two
inputs, restart the process, or run another command. Reply with only the
complete line that starts with `RESULT:`.
