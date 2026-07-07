"""OpenAI Responses API stream context with provider-specific state."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..base.context import StreamContext


@dataclass
class OpenAIResponsesStreamContext(StreamContext):
    """Stream context with OpenAI Responses API specific state.

    Extends the base StreamContext with fields needed for Responses API
    stream conversion, including output item tracking, text accumulation,
    and item-to-call-id resolution.

    Attributes:
        item_id_to_call_id: Reverse mapping from Responses item_id to
            tool call_id for function call argument delta resolution.
        output_item_emitted: Whether the initial output_item.added and
            content_part.added events have been emitted.
        item_id: Current output item ID for the response message.
        accumulated_text: Accumulated text deltas for the final
            response.completed payload.
        accumulated_reasoning: Accumulated reasoning deltas for the final
            response.completed payload.
        reasoning_seen: Whether a reasoning delta was observed, including an
            empty delta that must still be preserved for follow-up requests.
        reasoning_item_id: Synthetic Responses reasoning output item ID.
        reasoning_item_emitted: Whether the synthetic reasoning output_item.added
            event has been emitted.
        reasoning_item_done_emitted: Whether the synthetic reasoning
            output_item.done event has been emitted.
        content_part_done_emitted: Whether content_part.done has been
            emitted (prevents duplicate emission).
    """

    item_id_to_call_id: dict[str, str] = field(default_factory=dict)
    output_item_emitted: bool = False
    item_id: str = ""
    accumulated_text: str = ""
    accumulated_reasoning: str = ""
    reasoning_seen: bool = False
    reasoning_item_id: str = ""
    reasoning_item_emitted: bool = False
    reasoning_item_done_emitted: bool = False
    content_part_done_emitted: bool = False
    message_item_metadata: dict = field(default_factory=dict)
    passthrough_output_items: list[dict] = field(default_factory=list)
    tool_call_provider_metadata_map: dict[str, dict] = field(default_factory=dict)
    _sequence_number: int = 0

    @classmethod
    def from_base(cls, base: StreamContext) -> OpenAIResponsesStreamContext:
        """Create from a base StreamContext, preserving existing state.

        Args:
            base: The base StreamContext whose state should be carried over.

        Returns:
            A new OpenAIResponsesStreamContext with the base state copied.
        """
        ctx = cls()
        # Copy base StreamContext fields
        ctx.warnings = base.warnings
        ctx.options = base.options
        ctx.metadata = base.metadata
        ctx.response_id = base.response_id
        ctx.model = base.model
        ctx.created = base.created
        ctx.current_block_index = base.current_block_index
        ctx.tool_call_id_map = base.tool_call_id_map
        ctx.tool_call_item_id_map = base.tool_call_item_id_map
        ctx.pending_usage = base.pending_usage
        ctx.pending_finish = base.pending_finish
        ctx.pending_response = base.pending_response
        ctx._started = base._started
        ctx._ended = base._ended
        ctx._tool_call_args = base._tool_call_args
        ctx._tool_call_order = base._tool_call_order
        ctx._tool_call_types = base._tool_call_types
        if hasattr(base, "accumulated_reasoning"):
            ctx.accumulated_reasoning = base.accumulated_reasoning
        if hasattr(base, "reasoning_seen"):
            ctx.reasoning_seen = base.reasoning_seen
        if hasattr(base, "reasoning_item_id"):
            ctx.reasoning_item_id = base.reasoning_item_id
        if hasattr(base, "reasoning_item_emitted"):
            ctx.reasoning_item_emitted = base.reasoning_item_emitted
        if hasattr(base, "reasoning_item_done_emitted"):
            ctx.reasoning_item_done_emitted = base.reasoning_item_done_emitted
        if hasattr(base, "passthrough_output_items"):
            ctx.passthrough_output_items = base.passthrough_output_items
        if hasattr(base, "message_item_metadata"):
            ctx.message_item_metadata = base.message_item_metadata
        if hasattr(base, "tool_call_provider_metadata_map"):
            ctx.tool_call_provider_metadata_map = base.tool_call_provider_metadata_map
        return ctx

    def register_message_item_metadata(self, item: dict) -> None:
        """Remember Responses message output item metadata for round-trip."""
        if item_id := item.get("id"):
            self.item_id = item_id
        for key, value in item.items():
            if key not in {"content", "status"}:
                self.message_item_metadata[key] = value

    def register_tool_call_item(self, tool_call_id: str, item_id: str) -> None:
        """Register tool call item with reverse item_id mapping.

        Extends the base implementation to also populate
        ``item_id_to_call_id`` for Responses API delta resolution.

        Args:
            tool_call_id: The stable tool correlation identifier.
            item_id: The Responses output item identifier for the function call.
        """
        super().register_tool_call_item(tool_call_id, item_id)
        if tool_call_id and item_id:
            self.item_id_to_call_id[item_id] = tool_call_id

    def add_passthrough_output_item(self, item: dict) -> None:
        """Remember an opaque Responses output item for response.completed."""
        item_id = item.get("id")
        call_id = item.get("call_id")
        for idx, existing in enumerate(self.passthrough_output_items):
            if item_id and existing.get("id") == item_id:
                self.passthrough_output_items[idx] = dict(item)
                return
            if call_id and existing.get("call_id") == call_id:
                self.passthrough_output_items[idx] = dict(item)
                return
        self.passthrough_output_items.append(dict(item))

    def register_tool_call_provider_metadata(
        self, tool_call_id: str, metadata: dict
    ) -> None:
        """Remember provider metadata for a Responses tool call item."""
        if tool_call_id and metadata:
            self.tool_call_provider_metadata_map[tool_call_id] = dict(metadata)

    def get_tool_call_provider_metadata(self, tool_call_id: str) -> dict:
        """Return provider metadata for a Responses tool call item."""
        return self.tool_call_provider_metadata_map.get(tool_call_id, {})
