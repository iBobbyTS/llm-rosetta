"""Profile-backed OpenAI Images API routing for Codex image_gen.imagegen."""

from __future__ import annotations

from typing import Any

from codex_rosetta.routing import ResolvedRoute

from .transport import ProviderInfo
from .transport.provider_info import openai_auth

IMAGE_ENDPOINTS = frozenset({"images/generations", "images/edits"})
IMAGEGEN_PROFILE_ITEM_ID = "namespace.image_gen.imagegen"


class CodexImageConfigurationError(ValueError):
    """Raised when Modified image generation lacks a usable OpenAI endpoint."""


def profile_image_provider(
    route: ResolvedRoute,
    *,
    proxy_url: str | None,
) -> ProviderInfo:
    """Build the OpenAI Images provider declared by the selected Tool Profile."""
    values: dict[str, Any] = route.tool_profile_inputs.get(IMAGEGEN_PROFILE_ITEM_ID, {})
    base_url = str(values.get("base_url", "")).strip()
    token = str(values.get("token", "")).strip()
    if not base_url:
        raise CodexImageConfigurationError(
            "image_gen.imagegen Modified requires a Base URL"
        )
    if not token:
        raise CodexImageConfigurationError(
            "image_gen.imagegen Modified requires a Token"
        )
    try:
        return ProviderInfo(
            "image_gen.imagegen",
            api_key=token,
            base_url=base_url,
            auth_header_fn=openai_auth,
            url_template="{base_url}",
            proxy_url=proxy_url,
        )
    except ValueError as exc:
        raise CodexImageConfigurationError(str(exc)) from exc


def image_trace_summary(upstream_path: str, provider: ProviderInfo) -> dict[str, str]:
    """Return a secret-free Gateway Logs summary for one Images API request."""
    return {
        "endpoint": upstream_path,
        "executor": "openai_images_api",
        "base_url": provider.base_url,
    }
