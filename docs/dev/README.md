# Codex-Rosetta Developer Documentation

Developer documentation is maintained in English. User-facing documentation
lives in [`docs/en`](../en/README.md) and [`docs/zh-cn`](../zh-cn/README.md).

## Compatibility

- [Codex source compatibility](version-compatibility/README.md)
- [Codex model catalog field reference](../en/codex-model-catalog.md)
- [Compatibility points](version-compatibility/compatibility-points.md)
- [Rosetta compatibility source map](version-compatibility/rosetta-source-map.md)
- [Upgrade checklist](version-compatibility/upgrade-checklist.md)
- [Compatibility evidence](version-compatibility/evidence/README.md)
- [Upgrade reports](version-compatibility/reports/README.md)
- [Current alpha.23 source-first review](version-compatibility/reports/upgrade-review.md)

## Architecture and research

- [Design history](design/architecture.md)
- [Provider and model parameter survey](provider_model_params/survey.md)
- [SDK and IR research](sdk_ir/)
- [Codex tool localization trace QA](codex-tool-localization/trace-qa.md)
- [Real-agent tool-use testing](agent-tool-testing.md)

## Release

- [Manual GitHub Release procedure](releasing.md)

Releases are created only through the GitHub web UI. PyPI and Docker publishing
targets are disabled. `make build-docker` remains available for local
verification and always rebuilds/installs the wheel from the current checkout.
`make compose-up` applies the same provenance rule to the versioned Compose
service and never pulls a published Codex-Rosetta image.

## Manual development deployment

The manual development deployment path remains available:

```bash
make deploy-dev SSH_TARGET=cloud.usa2
```

`deploy-dev` builds from the current working tree rather than the committed
state. Pull the intended branch and verify that `src/` contains no unintended
changes before deploying. The command builds a development wheel and Docker
image, then sends it to the configured remote stack over SSH.
