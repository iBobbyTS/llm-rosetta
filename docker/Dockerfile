# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.14.6
ARG REGISTRY_MIRROR
FROM ${REGISTRY_MIRROR:-docker.io}/python:${PYTHON_VERSION}-alpine AS base

ARG PYPI_MIRROR
ARG LOCAL_WHEEL

# Prevents Python from writing pyc files and enables unbuffered output.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Create a non-privileged user and group (alpine syntax).
RUN addgroup -g 1000 appgroup && \
    adduser -D -u 1000 -G appgroup appuser

# Install su-exec for runtime user switching in entrypoint.
RUN apk add --no-cache su-exec

# Copy only the dist/ directory for local wheels (never copy the full tree —
# even with .dockerignore, a broad COPY risks leaking secrets into layers).
# The trailing slash on dist/ + a wildcard ensures COPY is a no-op when empty.
RUN mkdir -p /tmp/dist/
COPY dist/ /tmp/dist/

# The application itself must come from the current repository build. PyPI is
# used only to resolve third-party dependencies declared by that wheel.
RUN test -n "$LOCAL_WHEEL" && test -f "/tmp/dist/$LOCAL_WHEEL" && \
    echo "Installing from current local wheel: $LOCAL_WHEEL" && \
    if [ -n "$PYPI_MIRROR" ]; then \
        pip install -i "$PYPI_MIRROR" --no-cache-dir "/tmp/dist/${LOCAL_WHEEL}[gateway,profiling]"; \
    else \
        pip install --no-cache-dir "/tmp/dist/${LOCAL_WHEEL}[gateway,profiling]"; \
    fi && \
    (pip uninstall -y pip setuptools 2>/dev/null || true) && \
    rm -rf /tmp/dist/

# Config directory — mount a volume here with your config.jsonc.
# The gateway also stores metrics and request logs under /config/data/.
RUN mkdir -p /config && chown appuser:appgroup /config
VOLUME /config

# Entrypoint fixes /config ownership and drops to appuser via su-exec.
# Set PUID/PGID environment variables to match your host user (default: 1000).
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8765

ENTRYPOINT ["/entrypoint.sh"]
CMD ["codex-rosetta-gateway", "--config", "/config/config.jsonc", "--host", "0.0.0.0"]
