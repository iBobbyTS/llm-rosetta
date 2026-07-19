# Codex CLI Version Compatibility

Codex-Rosetta release numbers follow the Codex CLI version they support, with
an additional Rosetta patch suffix. A release version has the form
`{codex_version}.r{patch_number}`: the Codex version identifies the compatible
Codex CLI release, while `rN` identifies a Rosetta patch for that same Codex
release. New Codex releases start at `r0`; subsequent Rosetta-only fixes
increment `rN`.

For example, `0.144.0.r0` is the first Codex-Rosetta release compatible with
Codex CLI `0.144.0`. The source string retains the `rN` spelling; Python
package metadata normalizes it to the PEP 440 equivalent `.postN`.

## Current Compatibility

Codex-Rosetta remains at `0.144.0.r0`. The source-first adaptation for Codex
`0.145.0-alpha.23` is implemented and was tested with a binary built from the
exact target source, but failed or unavailable live gates prevent package
adoption. The installed CLI is `0.144.6`; it was not used as evidence for the
alpha target's behavior.

See the [developer compatibility record](../dev/version-compatibility/README.md)
and the [alpha.23 upgrade report](../dev/version-compatibility/reports/upgrade-review.md)
for exact source commits, contract failures, open compatibility points, and
required live gates.
