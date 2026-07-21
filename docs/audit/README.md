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
- Latest run: [`runs/20260721-1232/REPORT.md`](runs/20260721-1232/REPORT.md)
- Latest status: AUD-019/AUD-021 and sub-findings AUD-022/AUD-023/AUD-024 are closed at deterministic evidence depth. The owner selected explicit rejection for `computer_call_output`; no generic computer-control support was added. The remediation focused suite reports `248 passed`, the full suite `3624 passed, 5 skipped`, and `make lint` passes. No real provider/API/Codex call or deployment occurred.

Historical run snapshots remain under their original dated directories. They
are preserved as historical evidence and may contain paths or conclusions that
were true before this current baseline; they are not current status.
