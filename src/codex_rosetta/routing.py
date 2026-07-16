"""Routing layer — model/provider/shim resolution contract.

Defines the :class:`ResolvedRoute` data contract and :class:`Router`
protocol for resolving a model name into a target provider, shim config,
and preset-defined input modalities.

This module lives in the core library (no network or gateway deps) so
any consumer (gateway, argo-proxy, CLI tools) can depend on it.

Transport config (e.g. :class:`~gateway.transport.ProviderInfo`) is
**not** part of the route — it's transport-specific and returned
separately by each :class:`Router` implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from codex_rosetta.auto_detect import ProviderType


@dataclass(slots=True, frozen=True)
class ResolvedRoute:
    """Result of resolving a model name to a target route.

    Contains all the information the pipeline and transport layers need
    to process a request, except transport-specific config (e.g.
    ``ProviderInfo``), which is returned alongside by the router.

    Attributes:
        source_provider: API standard of the incoming request
            (e.g. ``"openai_chat"``).
        target_provider: API standard of the upstream provider
            (e.g. ``"anthropic"``).
        provider_name: User-configured provider name
            (e.g. ``"deepseek"``, ``"my-openai"``).
        shim_name: Shim/type identifier for transform lookup
            (e.g. ``"volcengine--openai_chat"``), or ``None``.
        upstream_model: Actual model ID to send upstream, or ``None``
            when the gateway model name is used as-is.
        input_modalities: Input modalities declared by the bundled model preset,
            or ``None`` when the routed model is unknown.
        tool_profile_name: Selected tool-profile identifier, or ``None`` when
            tool profiles do not apply to the route.
        tool_profile: Effective catalog item states for the selected profile.
        tool_profile_inputs: Function-card input values for the selected profile.
        responses_processing: Internal handling mode for same-format OpenAI
            Responses routes. This does not represent a distinct wire protocol.
        tool_runtime_capabilities: Gateway-owned optional tool executors available
            to this route. Core converters treat these as opaque capability names.
    """

    source_provider: ProviderType
    target_provider: ProviderType
    provider_name: str
    shim_name: str | None = None
    upstream_model: str | None = None
    input_modalities: list[str] | None = None
    tool_profile_name: str | None = None
    tool_profile: dict[str, str] = field(default_factory=dict)
    tool_profile_inputs: dict[str, dict[str, str]] = field(default_factory=dict)
    responses_processing: Literal["passthrough", "rosetta"] = "rosetta"
    tool_runtime_capabilities: frozenset[str] = field(default_factory=frozenset)


class Router(Protocol):
    """Interface for resolving a model name to a target route.

    Each deployment (gateway, argo-proxy, etc.) provides its own
    implementation backed by its config system.  The pipeline and
    transport layers consume the resulting :class:`ResolvedRoute`
    without knowing how it was produced.
    """

    def resolve(self, source_provider: ProviderType, model: str) -> ResolvedRoute:
        """Resolve *model* to a :class:`ResolvedRoute`.

        Implementations may return additional transport-specific config
        (e.g. ``tuple[ResolvedRoute, ProviderInfo]``), but the
        protocol contract only guarantees ``ResolvedRoute``.  Callers
        that need transport config should use the concrete type.

        Args:
            source_provider: API standard of the incoming request.
            model: Model name as specified by the client.

        Returns:
            A :class:`ResolvedRoute` with target provider, shim,
            and capabilities.

        Raises:
            KeyError: If the model is not in the routing table.
        """
        ...


def is_openai_responses_passthrough(route: ResolvedRoute) -> bool:
    """Return whether *route* is a direct OpenAI Responses pass-through."""

    return (
        route.responses_processing == "passthrough"
        and route.source_provider in ("openai_responses", "open_responses")
        and route.target_provider in ("openai_responses", "open_responses")
    )
