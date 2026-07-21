# Versioned Audit Evidence

This directory is the canonical home for persistent audit ledgers, findings,
coverage, system mapping, and run evidence. The approved project profile is
the single file at [`../audit-profile.md`](../audit-profile.md); do not create a
second profile under this directory.

## Current baseline

- Profile: [`../audit-profile.md`](../audit-profile.md)
- System map: [`SYSTEM-MAP.md`](SYSTEM-MAP.md)
- Coverage ledger: [`COVERAGE.md`](COVERAGE.md)
- Findings ledger: [`FINDINGS.md`](FINDINGS.md)
- Latest run: [`runs/20260720-1606/REPORT.md`](runs/20260720-1606/REPORT.md)
- Latest status: AUD-015 and AUD-016 are closed deterministically after authorized remediation, adversarial targeted re-audit, `158` focused tests, lint/type checks, and the `3542`-test non-integration suite. No real provider/API call was made.

Historical run snapshots remain under their original dated directories. They
are preserved as historical evidence and may contain paths or conclusions that
were true before this current baseline; they are not current status.
