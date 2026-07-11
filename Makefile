# Makefile for codex-rosetta package

# Variables
PACKAGE_NAME := codex-rosetta
DOCKER_IMAGE := oaklight/codex-rosetta-gateway
DIST_DIR := dist
VERSION := $(shell grep -oE '__version__[[:space:]]*=[[:space:]]*"[^"]+"' src/codex_rosetta/__init__.py | grep -oE '"[^"]+"' | tr -d '"' || echo "0.1.0")

# Optional variables
V ?= $(VERSION)
PYPI_MIRROR ?=
REGISTRY_MIRROR ?=
CODEX_SOURCE ?= ../openai-codex-src
CODEX_CONTRACT_BASELINE ?= docs/dev/version-compatibility/codex-source-contract.json
CODEX_CONTRACT_SCRIPT := scripts/check_codex_compatibility.py
RELEASE_VERSION_SCRIPT := scripts/check_release_version.py
CODEX_JSONL_ANALYZER := scripts/analyze_codex_jsonl_errors.py
PY_CHECK_PATHS := src/ tests/ $(CODEX_CONTRACT_SCRIPT) $(RELEASE_VERSION_SCRIPT) $(CODEX_JSONL_ANALYZER)

# Default target
all: lint test build

# ──────────────────────────────────────────────
# Linting & Formatting
# ──────────────────────────────────────────────

# Run ruff linter
lint:
	@echo "Running ruff check..."
	ruff check $(PY_CHECK_PATHS)
	@echo "Running ruff format check..."
	ruff format --check $(PY_CHECK_PATHS)
	@echo "Running ty check..."
	ty check
	@echo "Checking complexity ratchet..."
	complexipy --quiet
	@echo "Lint complete."

# Auto-fix lint issues
lint-fix:
	@echo "Auto-fixing lint issues..."
	ruff check --fix $(PY_CHECK_PATHS)
	ruff format $(PY_CHECK_PATHS)
	@echo "Lint fix complete."

# ──────────────────────────────────────────────
# Testing
# ──────────────────────────────────────────────

# Run tests
test:
	@echo "Running tests..."
	pytest tests/ --ignore=tests/integration -v --tb=short
	@echo "Tests completed."

# Run integration tests (requires API keys; uses proxychains if available)
test-integration:
	@echo "Running integration tests..."
	@if command -v proxychains >/dev/null 2>&1; then \
		echo "(using proxychains)"; \
		proxychains -q pytest tests/integration/ -v --tb=short; \
	else \
		pytest tests/integration/ -v --tb=short; \
	fi
	@echo "Integration tests completed."

# Run gateway integration tests (all SDKs × all models via llm_api_simple_tests)
test-gateway:
	@echo "Running gateway integration tests..."
	@./scripts/run_gateway_integration.sh
	@echo "Gateway integration tests completed."

# Compare the current sibling Codex checkout against the reviewed source contract.
check-codex-compat:
	@echo "Checking Codex source compatibility contract..."
	python $(CODEX_CONTRACT_SCRIPT) \
		--source $(CODEX_SOURCE) \
		--baseline $(CODEX_CONTRACT_BASELINE)

# Refresh only after reviewing the reported Codex source contract changes.
update-codex-compat-baseline:
	python $(CODEX_CONTRACT_SCRIPT) \
		--source $(CODEX_SOURCE) \
		--baseline $(CODEX_CONTRACT_BASELINE) \
		--write-baseline

# Validate the exact source/tag contract before creating a manual GitHub Release.
check-release-version:
	@test -n "$(RELEASE_TAG)" || (echo "RELEASE_TAG is required" >&2; exit 1)
	python $(RELEASE_VERSION_SCRIPT) --tag "$(RELEASE_TAG)"

# ──────────────────────────────────────────────
# Package targets
# ──────────────────────────────────────────────

# Build the Python package
build-package: clean-package
	@echo "Building $(PACKAGE_NAME) package..."
	python -m build
	@echo "Build complete. Distribution files are in $(DIST_DIR)/"

# Publishing is intentionally disabled. Releases are created manually in the
# GitHub UI; this repository does not publish PyPI or Docker artifacts.
push-package:
	@echo "Disabled: create releases manually in the GitHub UI; PyPI publishing is not configured." >&2
	@false

# Clean up build and distribution files
clean-package:
	@echo "Cleaning up build and distribution files..."
	rm -rf $(DIST_DIR) *.egg-info build/
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	@echo "Cleanup complete."

# Aliases
build: build-package
push: push-package
clean: clean-package

# ──────────────────────────────────────────────
# Docker
# ──────────────────────────────────────────────

build-docker: build-package
	@echo "Building Docker image $(DOCKER_IMAGE):$(V)..."
	@BUILD_ARGS=""; \
	if [ -n "$(REGISTRY_MIRROR)" ]; then \
		echo "Using registry mirror: $(REGISTRY_MIRROR)"; \
		BUILD_ARGS="$$BUILD_ARGS --build-arg REGISTRY_MIRROR=$(REGISTRY_MIRROR)"; \
	fi; \
	LOCAL_WHEEL=$$(ls dist/*.whl | head -n 1 | xargs basename); \
	test -n "$$LOCAL_WHEEL" || (echo "Local wheel build did not produce dist/*.whl" >&2; exit 1); \
	echo "Using current local wheel: $$LOCAL_WHEEL"; \
	BUILD_ARGS="$$BUILD_ARGS --build-arg LOCAL_WHEEL=$$LOCAL_WHEEL"; \
	if [ -n "$(PYPI_MIRROR)" ]; then \
		echo "Using PyPI mirror: $(PYPI_MIRROR)"; \
		BUILD_ARGS="$$BUILD_ARGS --build-arg PYPI_MIRROR=$(PYPI_MIRROR)"; \
	fi; \
	cd docker && docker build -f Dockerfile $$BUILD_ARGS -t $(DOCKER_IMAGE):$(V) -t $(DOCKER_IMAGE):latest ..
	@echo "Docker image built successfully."

compose-up: build-package
	@LOCAL_WHEEL=$$(ls dist/*.whl | head -n 1 | xargs basename); \
	test -n "$$LOCAL_WHEEL" || (echo "Local wheel build did not produce dist/*.whl" >&2; exit 1); \
	echo "Building Compose service from current local wheel: $$LOCAL_WHEEL"; \
	LOCAL_WHEEL="$$LOCAL_WHEEL" CODEX_ROSETTA_VERSION="$(VERSION)" \
		docker-compose -f docker/docker-compose.yaml up --build -d

push-docker:
	@echo "Disabled: this repository does not publish Docker images." >&2
	@false

clean-docker:
	@echo "Cleaning Docker images..."
	docker rmi $(DOCKER_IMAGE):latest 2>/dev/null || true
	docker rmi $(DOCKER_IMAGE):$(V) 2>/dev/null || true

# ──────────────────────────────────────────────
# Dev-test deployment
# ──────────────────────────────────────────────

SSH_TARGET ?=
DEVTEST_STACK ?= /dockervol/dockge/stacks/codex-rosetta-devtest
DEVTEST_CONTAINER ?= codex-rosetta-devtest-codex-rosetta-gateway-devtest-1

deploy-dev:
ifndef SSH_TARGET
	$(error SSH_TARGET is required. Usage: make deploy-dev SSH_TARGET=cloud.usa2)
endif
	@set -e; \
	COMMIT=$$(git rev-parse --short HEAD); \
	ORIG_VER=$$(python -c 'import re; print(re.search(r"__version__ = \"([^\"]+)\"", open("src/codex_rosetta/__init__.py").read()).group(1))'); \
	DEV_VER="$$ORIG_VER.dev0+g$$COMMIT"; \
	restore_version() { \
		ORIG_VER="$$ORIG_VER" DEV_VER="$$DEV_VER" python -c 'import os; from pathlib import Path; p=Path("src/codex_rosetta/__init__.py"); s=p.read_text(); p.write_text(s.replace("__version__ = \"" + os.environ["DEV_VER"] + "\"", "__version__ = \"" + os.environ["ORIG_VER"] + "\""))'; \
	}; \
	trap restore_version EXIT HUP INT TERM; \
	echo "==> Building dev wheel $$DEV_VER..."; \
	python -c 'from pathlib import Path; p=Path("src/codex_rosetta/__init__.py"); s=p.read_text(); p.write_text(s.replace("__version__ = \"'"$$ORIG_VER"'\"", "__version__ = \"'"$$DEV_VER"'\""))'; \
	rm -rf dist build; \
	conda run -n llm-rosetta python -m build --wheel -q; \
	restore_version; \
	trap - EXIT HUP INT TERM; \
	WHEEL=$$(ls dist/*.whl | head -1 | xargs basename); \
	echo "==> Building Docker image from $$WHEEL..."; \
	docker build -f docker/Dockerfile --build-arg LOCAL_WHEEL=$$WHEEL -t $(DOCKER_IMAGE):dev-test -q .; \
	echo "==> Deploying to $(SSH_TARGET) via zstd..."; \
	docker save $(DOCKER_IMAGE):dev-test | zstd -3 | ssh $(SSH_TARGET) \
		'zstd -d | docker load && \
		 cd $(DEVTEST_STACK) && \
		 docker compose up -d --force-recreate && \
		 sleep 3 && \
		 curl -sS http://127.0.0.1:54982/health && echo && \
		 docker exec $(DEVTEST_CONTAINER) python -c "import codex_rosetta; print(codex_rosetta.__version__)"'; \
	echo "==> Dev-test deployed successfully."

# Help target
help:
	@echo "Available targets:"
	@echo ""
	@echo "Development:"
	@echo "  lint           - Run Ruff lint/format checks and ty type checking"
	@echo "  lint-fix       - Auto-fix lint and formatting issues"
	@echo "  test               - Run unit tests with pytest"
	@echo "  test-integration   - Run integration tests via proxychains"
	@echo "  test-gateway       - Run gateway integration tests (all SDKs × all models)"
	@echo "  check-codex-compat - Compare ../openai-codex-src with the reviewed contract"
	@echo "  update-codex-compat-baseline - Refresh the reviewed Codex contract snapshot"
	@echo ""
	@echo "Package:"
	@echo "  build-package  - Build the Python package"
	@echo "  push-package   - Disabled (manual GitHub Release only)"
	@echo "  clean-package  - Clean up build and distribution files"
	@echo ""
	@echo "Docker:"
	@echo "  build-docker   - Build Docker image (local x64)"
	@echo "  compose-up     - Rebuild the current checkout wheel and start local Compose"
	@echo "  push-docker    - Disabled (Docker publishing is not configured)"
	@echo "  clean-docker   - Clean Docker images"
	@echo ""
	@echo "Aliases:"
	@echo "  build          - Alias for build-package"
	@echo "  push           - Disabled alias for push-package"
	@echo "  check-release-version RELEASE_TAG=v<version>.rN - Validate manual release tag"
	@echo "  clean          - Alias for clean-package"
	@echo ""
	@echo "Composite targets:"
	@echo "  all            - Run lint, test, and build (default)"
	@echo ""
	@echo "Usage examples:"
	@echo "  make build-docker                  # rebuild local wheel, then build image"
	@echo "  make build-docker V=0.144.0.r0     # local wheel, image tag=0.144.0.r0"
	@echo "  make build-docker V=dev-test       # local wheel, image tag=dev-test"
	@echo "  make build-docker PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
	@echo "  make build-docker REGISTRY_MIRROR=docker.1ms.run"
	@echo ""
	@echo "Variables:"
	@echo "  V=<version|tag>          - Docker image tag (default: source version)"
	@echo "  PYPI_MIRROR=<url>        - Mirror used only for wheel dependencies"
	@echo "  REGISTRY_MIRROR=<host>   - Docker registry mirror"
	@echo ""
	@echo "Deployment:"
	@echo "  deploy-dev     - Build dev image and deploy to remote dev-test gateway"
	@echo ""
	@echo "  SSH_TARGET=<host>        - SSH target for deploy-dev (required)"
	@echo "  DEVTEST_STACK=<path>     - Remote compose stack path (default: /dockervol/dockge/stacks/codex-rosetta-devtest)"
	@echo ""
	@echo "Usage examples:"
	@echo "  make deploy-dev SSH_TARGET=cloud.usa2"
	@echo ""
	@echo "Detected version: $(VERSION)"

.PHONY: all lint lint-fix test test-integration test-gateway check-codex-compat update-codex-compat-baseline check-release-version build-package push-package clean-package build push clean build-docker compose-up push-docker clean-docker deploy-dev help
