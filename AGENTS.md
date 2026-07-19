# AGENTS.md — Codex-Rosetta

> Context file for AI coding assistants. Symlinked as `CLAUDE.md`.

## What this project is

Codex-Rosetta is a **Python library for converting between different LLM provider
API formats** using a hub-and-spoke architecture with a central IR (Intermediate
Representation). It solves the N² provider conversion problem — each provider
only needs a single converter to/from IR, and any-to-any conversion is
automatically supported.

The project also ships an **API gateway** (`codex-rosetta-gateway`) that proxies
requests between providers with live format conversion, streaming, and key
rotation.

Zero required dependencies at its core; provider SDKs are optional extras.

## Architecture

### Conversion pipeline

```
Provider A ──→ IR ──→ Provider B
```

Four converters, one per API standard:

| Converter | API Standard | Module |
|-----------|-------------|--------|
| `openai_chat` | OpenAI Chat Completions | `converters/openai_chat/` |
| `openai_responses` | OpenAI Responses API | `converters/openai_responses/` |
| `anthropic` | Anthropic Messages API | `converters/anthropic/` |
| `google` | Google GenAI API | `converters/google_genai/` |

Each converter implements bidirectional conversion (request/response) and
streaming. Converters are provider-agnostic — provider-specific quirks are
handled by the **shim layer**.

### Shim layer

A **ProviderShim** is a lightweight identity card that declares which base
converter a provider uses, plus connection defaults and field-level transforms.
This avoids per-provider conversion code — converters stay generic, shims
declare the differences.

| Component | File | Purpose |
|-----------|------|---------|
| `ProviderShim` / `ModelShim` | `shims/provider_shim.py` | Data classes + registry |
| `Transform` primitives | `shims/transforms.py` | `strip_fields`, `rename_field`, `set_defaults` |
| Built-in shims | `shims/builtins.py` | OpenAI, Anthropic, Google, DeepSeek, Volcengine |

### Gateway

The gateway is a standalone HTTP proxy server that accepts requests in any
supported format and forwards them to any configured upstream provider.

| Module | Purpose |
|--------|---------|
| `gateway/app.py` | Route handlers, app factory |
| `gateway/proxy.py` | Core proxy engine (non-streaming, streaming, SSE, transforms) |
| `gateway/config.py` | JSONC config loading, env-var substitution |
| `gateway/providers.py` | Provider info, auth headers, key rotation |
| `gateway/admin/` | Admin UI, metrics, request logging, persistence |

### IR type system

Typed dataclasses for the intermediate representation live in `types/ir/`.
Provider-specific types (for documentation, not runtime) in
`types/anthropic/`, `types/google/`.

## Repository layout

```
src/codex_rosetta/
├── __init__.py              # Public API: convert(), get_converter_for_provider()
├── auto_detect.py           # Provider auto-detection from request body
├── tool_ops.py              # Cross-provider tool call utilities
├── converters/              # 4 bidirectional converters
│   ├── base/                # Abstract base + ConversionContext
│   ├── openai_chat/
│   ├── openai_responses/
│   ├── anthropic/
│   └── google_genai/
├── shims/                   # Provider/model identity cards + transforms
│   ├── provider_shim.py
│   ├── transforms.py
│   └── builtins.py
├── gateway/                 # HTTP proxy gateway
│   ├── app.py, proxy.py, config.py, providers.py
│   ├── auth.py, logging.py, cli.py, banner.py
│   └── admin/               # Admin panel (metrics, request log, persistence)
├── types/                   # Typed IR and provider-specific types
│   └── ir/                  # IR dataclasses (messages, parts, tools, stream)
└── _vendor/                 # Vendored dependencies (DO NOT EDIT)

tests/
├── converters/              # Per-converter test suites
├── gateway/                 # Gateway unit tests
├── integration/             # E2E tests (require API keys)
├── test_types/              # IR type system tests
├── test_shims.py
├── test_transforms.py
├── test_auto_detect.py
└── test_tool_ops.py

docs/
├── en/                      # English user documentation
├── zh-cn/                   # Chinese user documentation (matching paths)
└── dev/                     # English-only developer documentation
docker/                      # Dockerfile for gateway image
examples/                    # Usage examples
```

## Setup and commands

```bash
conda activate llm-rosetta
pip install -e ".[all]"
```

Run `make help` for all targets. Key ones:

```bash
make lint          # ruff check + ruff format --check + ty check
make lint-fix      # ruff check --fix + ruff format
make test          # pytest tests/ --ignore=tests/integration -v
make build         # python -m build
make push          # disabled; GitHub Releases are created manually
make build-docker  # Build local gateway image from the current checkout wheel
```

Tooling config (ruff, ty, complexipy) lives in `pyproject.toml`.

## Definition of done

1. `make lint` and `make test` exit 0
2. `ruff check --fix && ruff format` applied to changed files
3. New code has tests in `tests/`
4. Google-style docstrings on public APIs; comments in English
5. No manual edits to `_vendor/` — update upstream, re-vendor
6. Python 3.14.6 compatibility

## Integration testing with agentabi

After gateway or converter changes that affect cross-format conversion,
run the `agentabi` test matrix to verify end-to-end behavior with real
agent CLIs. This requires a running proxy (e.g. argo-proxy or
codex-rosetta-gateway) with upstream access.

### Setup

```bash
conda activate agentabi
# Ensure the proxy is running (e.g. on :44497)
```

### Test matrix

```python
from agentabi import run_sync

PROXY_ENV = {
    "OPENAI_BASE_URL": "http://127.0.0.1:44497/v1",
    "OPENAI_API_KEY": "<token>",
}

tests = [
    # (agent, model, prompt) — covers same-format and cross-format paths
    ("codex", "gpt-5-nano", "What is 2+2? Reply with just the number."),
    ("codex", "claude-haiku-4-5", "What is 3+3? Reply with just the number."),
    ("codex", "claude-opus-4-6", "What is 4+4? Reply with just the number."),
    ("opencode", "gpt-5-nano", "What is 5+5? Reply with just the number."),
]

for agent, model, prompt in tests:
    result = run_sync(prompt, agent=agent, model=model, env=PROXY_ENV,
                      max_turns=1, timeout=60)
    print(f"{agent}/{model}: {result.get('status')} → {result.get('result_text')}")
```

### Multi-turn tool-use test

For deeper validation (file reads, writes, multi-step reasoning):

```python
result = run_sync(
    "Read src/codex_rosetta/shims/provider_shim.py and write a summary to /tmp/test-output.md",
    agent="codex", model="claude-haiku-4-5",
    env=PROXY_ENV, max_turns=10, timeout=120,
    working_dir="/path/to/codex-rosetta",
)
# Check /tmp/test-output.md was created with meaningful content
```

### What to verify

- Same-format pass-through (OpenAI → OpenAI)
- Cross-format conversion (OpenAI Responses → Anthropic, via shim)
- Streaming SSE round-trip
- Multi-turn tool calls (file read/write)
- Reasoning/thinking fields preserved across formats

## Workflow

- **Branch from main**, open a PR, require CI green before merge.
- **Merge strategy: rebase** — keep commits atomic and well-messaged.
- Branch naming: `feature/...`, `fix/...`, `refactor/...`, `test/...`, `docs/...`
- Never force-push to `main`.
- **No AI co-author tags in commits.** Do not add `Co-authored-by` lines for AI
  tools in git commit messages. Disclose AI usage in PR descriptions instead.

## Documentation

Documentation is versioned on `main` under `docs/`:

- `docs/en/` contains English user documentation.
- `docs/zh-cn/` contains the matching Chinese user documentation.
- `docs/dev/` contains developer documentation and is English only.

### Cross-language consistency (enforced)

**Both language versions must be updated in the same task/agent run.** Never
update only one language and leave the other for later. The workflow is:

1. Make user-facing changes under `docs/en/` first (English is the source of truth).
2. Apply the equivalent change at the same relative path under `docs/zh-cn/`.
3. Update related English developer references under `docs/dev/` when needed.

The existing co-located module and example READMEs remain outside this
centralized documentation workflow. Manual development deployment guidance is
in `docs/dev/README.md`.

## Escalation

- Converter output mismatch → check IR types in `types/ir/`, then the
  converter's `*_ops.py` modules
- Shim/transform issue → check `shims/builtins.py` and `shims/transforms.py`
- Gateway config issue → check `gateway/config.py` resolution order
  (shim → type → name fallback)
- `_vendor/` issues → never fix in-place; update upstream, re-vendor
- Integration test failure → likely missing API keys or network; these are
  excluded from `make test` by default
- Test failure after 3 attempts → stop, report full output
- Never: delete files to fix errors, skip tests, modify `_vendor/` directly

## Files to never edit

- `src/codex_rosetta/_vendor/**` — vendored dependencies, managed externally

## Codex source and version compatibility

- The local OpenAI Codex source checkout is at `../openai-codex-src` relative to
  this repository.
- Treat the installed Codex CLI version and the source checkout commit as two
  separate compatibility identifiers. The source manifests can contain
  development placeholders, so never infer the source revision from the CLI
  version alone.
- Codex-Rosetta versions use `{codex_version}.r{patch_number}`. Start each newly
  adopted Codex release at `r0` and increment `rN` only for Rosetta-only fixes.
  Keep the `rN` spelling in source; Python package metadata may normalize it to
  the equivalent PEP 440 `.postN` form.
- `docs/dev/version-compatibility/` is the sole owner of Codex upgrade
  procedures, compatibility points, source ownership, evidence, and reports.
  Do not maintain a second checklist elsewhere.
- Versioned Codex/Rosetta documents must not put a version number, release
  line, or prerelease marker in the filename. Use stable descriptive names such
  as `upgrade-review.md` and `live-evidence.md`; the file content must state the
  exact version and source identity it covers. Update every repository
  reference when renaming such a file.
- Before changing a Codex-facing request, response, stream, tool, session, or
  model-catalog behavior, read that directory's `README.md`,
  `rosetta-source-map.md`, `compatibility-points.md`, and
  `upgrade-checklist.md`.
- The developer selects routine versus full inventory review. Every Codex
  version update must follow the selected workflow, produce a report, and
  satisfy all triggered automation and real Codex/API gates before compatibility
  or a package-version advance can be claimed.
- During normal development, add, change, or retire Codex-specific points and
  their code/test owners in the centralized ledger and source map in the same
  task.
