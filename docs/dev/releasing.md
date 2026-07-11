# Manual Release Procedure

GitHub Releases are created manually in the GitHub web UI. This repository
does not use an automated release workflow and does not publish packages to
PyPI or images to a Docker registry. The `push-package`, `push-docker`, and
`push` Make targets are intentionally disabled.

## Version and tag contract

`src/codex_rosetta/__init__.py::__version__` uses
`{codex_version}.r{patch_number}`, while the GitHub Release tag keeps the
repository's historical `v` prefix: `v{codex_version}.r{patch_number}`. For
example, source version `0.144.0.r0` is released as tag `v0.144.0.r0`. Each
newly supported Codex release starts at `r0`; Rosetta-only fixes for that Codex
version increment `rN`.

Before creating a release, validate the exact tag:

```bash
make check-release-version RELEASE_TAG=v0.144.0.r0
```

## Required local gates

Run all of the following from a clean reviewed revision:

```bash
make lint
make test
make check-codex-compat
python -m build
```

Install the wheel into a clean Python 3.14.6 virtual environment, then verify the
core import and `codex-rosetta-gateway --version`.
Complete any real Codex/API tests triggered by the version-compatibility
checklist before claiming compatibility.

## Create and roll back the release

In GitHub, open **Releases**, choose **Draft a new release**, create the exact
validated tag from the reviewed commit, attach the locally verified source and
wheel artifacts if desired, and publish only after a human verifies the tag,
commit, compatibility report, and test evidence.

`make build-docker` is a local verification/development target only. It first
builds the current checkout's wheel and requires that wheel in the Docker build;
it never installs Codex-Rosetta from PyPI and does not publish the resulting
image.

The versioned Compose file follows the same local provenance rule. Run
`make compose-up` from the repository root to rebuild the wheel and start the
service. The Make target supplies `LOCAL_WHEEL` to Compose; there is no
published registry fallback.

For rollback, mark or delete the GitHub Release in the UI and document the
superseding `rN` release. Because this repository does not push PyPI or Docker
artifacts, those registries have no automated promotion or rollback path.
