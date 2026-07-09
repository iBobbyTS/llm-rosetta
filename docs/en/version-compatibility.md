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

Codex-Rosetta `0.144.0.r0` is compatible with Codex CLI `0.144.0`.
