"""Project selected Codex Code Mode tools into ordinary Chat functions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .tool_profiles import (
    apply_profile_tool_mutations,
    apply_view_image_detail_profile,
    route_tool_state,
    tool_profile_contract,
    view_image_detail_values,
)
from .web_run_capabilities import (
    WEB_RUN_PROFILE_ITEM_ID,
    project_modified_web_run_function,
    web_run_model_availability,
)


@dataclass(frozen=True)
class ExecToolProjection:
    """Declarative mapping between one Chat function and a nested exec tool."""

    item_id: str
    chat_name: str
    nested_name: str
    input_mode: str = "args"
    input_field: str = "input"
    output_mode: str = "text"
    model_visible: bool = True
    allowed_detail_values: tuple[str, ...] | None = None
    description_replaced_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecDescriptionSection:
    """One exact top-level tool section inside an exec description."""

    name: str
    heading_start: int
    body_start: int
    section_end: int
    raw: str
    body: str
    has_complete_declaration: bool


@dataclass(frozen=True)
class ExecToolDefinitionPlan:
    """Projected definitions and the exact source sections that produced them."""

    definitions: dict[str, dict[str, Any]]
    sections: dict[str, ExecDescriptionSection]
    duplicate_section_names: frozenset[str]


@dataclass(frozen=True)
class DiscoveredExecToolPlan:
    """Request-history definitions for deferred nested exec tools."""

    definitions: dict[str, dict[str, Any]]
    projections: dict[str, ExecToolProjection]


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


_TOKEN_RE = re.compile(
    r"(?P<comment>//[^\n]*)"
    r"|(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
    r"|(?P<number>-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(?P<identifier>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"|(?P<symbol>[{}:;?,|&<>\[\]()])"
)
_WHITESPACE_RE = re.compile(r"\s+")
_EXEC_SECTION_HEADING_RE = re.compile(r"(?m)^### `([^`]+)`(?: \(`[^`]+`\))?[ \t]*$")
_EXEC_DECLARATION_FENCE_RE = re.compile(r"(?m)^```ts[ \t]*$")
_EXEC_CLOSING_FENCE_RE = re.compile(r"(?m)^```[ \t]*$")
DEFERRED_EXEC_GUIDANCE = (
    "Some deferred nested tools may be omitted from this description."
)
ALL_TOOLS_SEARCH_CHAT_NAME = "tool_search"
ALL_TOOLS_SEARCH_RESULT_PROTOCOL = "codex_rosetta.all_tools_search.v1"
ALL_TOOLS_SEARCH_MAX_RESULT_CHARS = 24_000
ALL_TOOLS_SEARCH_PROJECTION = ExecToolProjection(
    item_id="synthetic.all_tools_search",
    chat_name=ALL_TOOLS_SEARCH_CHAT_NAME,
    nested_name="",
    input_mode="all_tools_search",
)
NODE_REPL_TOOL_NAMES = (
    "mcp__node_repl__js",
    "mcp__node_repl__js_reset",
    "mcp__node_repl__js_add_node_module_dir",
)
_NODE_REPL_EXEC_PROJECTIONS = {
    name: ExecToolProjection(
        item_id=f"deferred.{name}",
        chat_name=name,
        nested_name=name,
        output_mode="mcp_content",
    )
    for name in NODE_REPL_TOOL_NAMES
}
_MAX_DISCOVERED_TOOL_DESCRIPTION_CHARS = 65_536
_MAX_TOOL_RESULT_TEXT_CHARS = 262_144
_MAX_DISCOVERY_HISTORY_CALLS = 64


def _tokenize_typescript(source: str) -> list[_Token]:
    """Tokenize one rendered type without silently skipping unknown syntax."""
    tokens: list[_Token] = []
    index = 0
    while index < len(source):
        whitespace = _WHITESPACE_RE.match(source, index)
        if whitespace is not None:
            index = whitespace.end()
            continue
        match = _TOKEN_RE.match(source, index)
        if match is None:
            raise ValueError(f"unsupported TypeScript token at offset {index}")
        tokens.append(_Token(match.lastgroup or "", match.group()))
        index = match.end()
    return tokens


class _TypeScriptSchemaParser:
    """Parse the constrained TypeScript emitted by Codex Code Mode."""

    def __init__(self, source: str) -> None:
        self._tokens = _tokenize_typescript(source)
        self._index = 0

    def parse(self) -> dict[str, Any]:
        """Parse one complete TypeScript type into JSON Schema."""
        schema = self._parse_union()
        if self._peek() is not None:
            raise ValueError("unexpected trailing TypeScript tokens")
        return schema

    def _peek(self) -> _Token | None:
        return self._tokens[self._index] if self._index < len(self._tokens) else None

    def _take(self, value: str | None = None) -> _Token:
        token = self._peek()
        if token is None or (value is not None and token.value != value):
            raise ValueError(f"expected {value or 'token'}")
        self._index += 1
        return token

    def _accept(self, value: str) -> bool:
        token = self._peek()
        if token is None or token.value != value:
            return False
        self._index += 1
        return True

    def _parse_union(self) -> dict[str, Any]:
        choices = [self._parse_intersection()]
        while self._accept("|"):
            choices.append(self._parse_intersection())
        if len(choices) == 1:
            return choices[0]

        constants = [choice.get("const") for choice in choices]
        constant_types = [choice.get("type") for choice in choices]
        if (
            all(value is not None for value in constants)
            and len(set(constant_types)) == 1
        ):
            return {"type": constant_types[0], "enum": constants}
        return {"anyOf": choices}

    def _parse_intersection(self) -> dict[str, Any]:
        choices = [self._parse_postfix()]
        while self._accept("&"):
            choices.append(self._parse_postfix())
        if len(choices) == 1:
            return choices[0]
        return {"allOf": choices}

    def _parse_postfix(self) -> dict[str, Any]:
        schema = self._parse_primary()
        while self._accept("["):
            self._take("]")
            schema = {"type": "array", "items": schema}
        return schema

    def _parse_primary(self) -> dict[str, Any]:
        token = self._take()
        if token.value == "{":
            return self._parse_object()
        if token.value == "[":
            return self._parse_tuple()
        if token.value == "(":
            schema = self._parse_union()
            self._take(")")
            return schema
        if token.kind == "string":
            value = (
                json.loads(token.value)
                if token.value.startswith('"')
                else token.value[1:-1]
            )
            return {"type": "string", "const": value}
        if token.kind == "number":
            value = (
                float(token.value)
                if any(marker in token.value for marker in ".eE")
                else int(token.value)
            )
            return {"type": "number", "const": value}
        if token.kind == "identifier":
            return self._parse_identifier(token.value)
        raise ValueError(f"unsupported TypeScript type token {token.value!r}")

    def _parse_identifier(self, identifier: str) -> dict[str, Any]:
        if identifier in {"Array", "ReadonlyArray"} and self._accept("<"):
            items = self._parse_union()
            self._take(">")
            return {"type": "array", "items": items}
        if identifier == "Record" and self._accept("<"):
            self._parse_union()
            self._take(",")
            values = self._parse_union()
            self._take(">")
            return {"type": "object", "additionalProperties": values}
        if identifier == "string":
            return {"type": "string"}
        if identifier == "number":
            return {"type": "number"}
        if identifier == "boolean":
            return {"type": "boolean"}
        if identifier == "true":
            return {"type": "boolean", "const": True}
        if identifier == "false":
            return {"type": "boolean", "const": False}
        if identifier == "null":
            return {"type": "null"}
        if identifier == "never":
            return {"not": {}}
        if identifier in {"unknown", "any", "object"}:
            return {}
        raise ValueError(f"unsupported TypeScript type {identifier!r}")

    def _parse_object(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        comments: list[str] = []
        additional_properties: dict[str, Any] | None = None
        while not self._accept("}"):
            token = self._peek()
            if token is None:
                raise ValueError("unterminated object type")
            if token.kind == "comment":
                comments.append(self._take().value[2:].strip())
                continue
            if token.value == "[":
                if additional_properties is not None:
                    raise ValueError("duplicate object index signature")
                self._take("[")
                key_token = self._take()
                if key_token.kind != "identifier":
                    raise ValueError("invalid object index signature")
                self._take(":")
                self._take("string")
                self._take("]")
                self._take(":")
                additional_properties = self._parse_union()
                self._accept(";") or self._accept(",")
                comments.clear()
                continue
            name_token = self._take()
            if name_token.kind not in {"identifier", "string"}:
                raise ValueError("invalid object property")
            name = (
                json.loads(name_token.value)
                if name_token.kind == "string" and name_token.value.startswith('"')
                else name_token.value.strip("'\"")
            )
            optional = self._accept("?")
            self._take(":")
            property_schema = self._parse_union()
            if comments:
                property_schema = dict(property_schema)
                property_schema["description"] = "\n".join(comments)
                comments.clear()
            properties[name] = property_schema
            if not optional:
                required.append(name)
            self._accept(";") or self._accept(",")
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": (
                additional_properties if additional_properties is not None else False
            ),
        }
        if required:
            schema["required"] = required
        return schema

    def _parse_tuple(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        while not self._accept("]"):
            items.append(self._parse_union())
            if not self._accept(","):
                self._take("]")
                break
        return {
            "type": "array",
            "prefixItems": items,
            "minItems": len(items),
            "maxItems": len(items),
        }


def exec_tool_projections_for_route(route: Any) -> dict[str, ExecToolProjection]:
    """Return model-visible and internal Profile-owned exec projections."""
    projections: dict[str, ExecToolProjection] = {}
    for item_id, definition in tool_profile_contract()["exec_projections"].items():
        state = route_tool_state(route, item_id)
        model_visible = state in {"passthrough", "modified"}
        internal_when_disabled = definition.get("internal_when_disabled", False)
        if not model_visible and not (state == "disabled" and internal_when_disabled):
            continue
        projection_definition = {
            key: value
            for key, value in definition.items()
            if key != "internal_when_disabled"
        }
        projection = ExecToolProjection(
            item_id=item_id,
            model_visible=model_visible,
            allowed_detail_values=(
                view_image_detail_values(route)
                if item_id == "function.view_image"
                else None
            ),
            **projection_definition,
        )
        projections[projection.chat_name] = projection
    projections[ALL_TOOLS_SEARCH_CHAT_NAME] = ALL_TOOLS_SEARCH_PROJECTION
    return projections


def project_exec_tool_definitions(
    exec_description: str,
    projections: dict[str, ExecToolProjection],
    *,
    profile_route: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Build Chat function definitions from selected exec description sections."""
    return plan_exec_tool_definitions(
        exec_description,
        projections,
        profile_route=profile_route,
    ).definitions


def plan_exec_tool_definitions(
    exec_description: str,
    projections: dict[str, ExecToolProjection],
    *,
    profile_route: Any | None = None,
) -> ExecToolDefinitionPlan:
    """Build definitions and retain the unique source span for each success."""
    section_spans = _exec_description_section_spans(exec_description)
    sections_by_name: dict[str, list[ExecDescriptionSection]] = {}
    for section in section_spans:
        sections_by_name.setdefault(section.name, []).append(section)
    duplicate_names = frozenset(
        name for name, sections in sections_by_name.items() if len(sections) > 1
    )
    section_projections = dict(projections)
    all_tools_search = section_projections.pop(ALL_TOOLS_SEARCH_CHAT_NAME, None)
    definitions = _all_tools_search_definitions(exec_description, all_tools_search)
    projected_sections: dict[str, ExecDescriptionSection] = {}
    for chat_name, projection in section_projections.items():
        matching_sections = sections_by_name.get(projection.nested_name, [])
        if len(matching_sections) != 1:
            continue
        section = matching_sections[0]
        if not section.has_complete_declaration:
            continue
        parsed = _project_one_definition(section.body, projection)
        if parsed is not None:
            if profile_route is not None:
                parsed = dict(parsed)
                if (
                    projection.item_id == WEB_RUN_PROFILE_ITEM_ID
                    and route_tool_state(profile_route, projection.item_id)
                    == "modified"
                ):
                    search_available, browser_available = web_run_model_availability(
                        profile_route
                    )
                    projected_function = project_modified_web_run_function(
                        parsed["function"],
                        search_available=search_available,
                        browser_available=browser_available,
                    )
                    if projected_function is None:
                        continue
                    parsed["function"] = projected_function
                parsed["function"] = apply_profile_tool_mutations(
                    parsed["function"], projection.item_id, profile_route
                )
                if projection.item_id == "function.view_image":
                    parsed["function"] = apply_view_image_detail_profile(
                        parsed["function"], profile_route
                    )
            definitions[chat_name] = parsed
            projected_sections[chat_name] = section
    return ExecToolDefinitionPlan(
        definitions=definitions,
        sections=projected_sections,
        duplicate_section_names=duplicate_names,
    )


def build_exec_script(
    projection: ExecToolProjection,
    arguments: dict[str, Any],
) -> str:
    """Build deterministic JavaScript for one projected nested-tool call."""
    if projection.input_mode == "all_tools_search":
        return _build_all_tools_search_script(arguments)
    if projection.input_mode == "freeform":
        value = arguments.get(projection.input_field)
        if not isinstance(value, str):
            raise ValueError(
                f"{projection.chat_name} requires string field "
                f"'{projection.input_field}'"
            )
        nested_input: Any = value
    else:
        nested_input = arguments
    if projection.allowed_detail_values is not None and "detail" in nested_input:
        detail = nested_input["detail"]
        if detail not in projection.allowed_detail_values:
            raise ValueError(
                f"{projection.chat_name} detail must be one of "
                f"{list(projection.allowed_detail_values)}"
            )
    literal = _javascript_json_literal(nested_input)
    invocation = f"const result = await tools.{projection.nested_name}({literal});\n"
    if projection.output_mode == "mcp_content":
        return (
            invocation
            + """if (Array.isArray(result?.content)) {
  for (const item of result.content) {
    if (item?.type === "text") text(item.text);
    else if (item?.type === "image") image(item);
    else text(item);
  }
} else {
  text(result);
}
if (result?.isError) text({ isError: true });
"""
        )
    output_helper = {
        "text": "text",
        "image": "image",
        "generated_image": "generatedImage",
    }[projection.output_mode]
    return invocation + f"{output_helper}(result);\n"


def _all_tools_search_definition() -> dict[str, Any]:
    """Build the Chat function used to search Codex's live ALL_TOOLS catalog."""
    return {
        "type": "function",
        "function": {
            "name": ALL_TOOLS_SEARCH_CHAT_NAME,
            "description": (
                "Search deferred tools in Codex's live ALL_TOOLS runtime catalog. "
                "Use natural_language for capability searches and regex for exact "
                "name or declaration patterns. Results include complete tool "
                "descriptions and declarations. Supported Node REPL matches become "
                "structured functions on the next request; invoke other matches "
                "through the raw exec tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 500,
                        "description": (
                            "Capability, provider, tool name, or declaration text "
                            "to find. Regex patterns are limited to 200 characters."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 8,
                        "description": "Maximum number of matching tools to return.",
                    },
                    "search_mode": {
                        "type": "string",
                        "enum": ["natural_language", "regex"],
                        "default": "natural_language",
                        "description": (
                            "Natural-language token ranking or case-insensitive "
                            "JavaScript regular-expression matching."
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


def _all_tools_search_definitions(
    exec_description: str,
    projection: ExecToolProjection | None,
) -> dict[str, dict[str, Any]]:
    """Return the synthetic search definition only for a live deferred exec."""
    if (
        projection is None
        or projection.input_mode != "all_tools_search"
        or DEFERRED_EXEC_GUIDANCE not in exec_description
    ):
        return {}
    return {projection.chat_name: _all_tools_search_definition()}


def _build_all_tools_search_script(arguments: dict[str, Any]) -> str:
    """Build bounded JavaScript that searches only the live ALL_TOOLS array."""
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("tool_search requires a non-empty string field 'query'")
    search_mode = arguments.get("search_mode", "natural_language")
    if search_mode not in {"natural_language", "regex"}:
        raise ValueError(
            "tool_search search_mode must be 'natural_language' or 'regex'"
        )
    max_query_length = 200 if search_mode == "regex" else 500
    if len(query) > max_query_length:
        raise ValueError(
            f"tool_search {search_mode} query must be at most "
            f"{max_query_length} characters"
        )
    limit = arguments.get("limit", 8)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("tool_search limit must be an integer from 1 to 50")

    query_literal = _javascript_json_literal(query)
    mode_literal = _javascript_json_literal(search_mode)
    protocol_literal = _javascript_json_literal(ALL_TOOLS_SEARCH_RESULT_PROTOCOL)
    return f"""const query = {query_literal};
const searchMode = {mode_literal};
const limit = {limit};
const resultProtocol = {protocol_literal};
const maxResultChars = {ALL_TOOLS_SEARCH_MAX_RESULT_CHARS};
const catalog = Array.isArray(ALL_TOOLS) ? ALL_TOOLS : [];
const normalize = (value) => String(value ?? "").normalize("NFKC").toLowerCase();
const queryText = normalize(query);
let ranked;
if (searchMode === "regex") {{
  let pattern;
  try {{
    pattern = new RegExp(query, "i");
  }} catch (error) {{
    text({{
      protocol: resultProtocol,
      query,
      search_mode: searchMode,
      limit,
      total_matches: 0,
      returned_matches: 0,
      truncated: false,
      matches: [],
      error: {{ code: "invalid_pattern", message: String(error) }},
    }});
    exit();
  }}
  ranked = catalog
    .map((entry, index) => ({{ entry, index }}))
    .filter(({{ entry }}) =>
      pattern.test(String(entry.name ?? "")) ||
      pattern.test(String(entry.description ?? ""))
    );
}} else {{
  const tokens = [...new Set(queryText.match(/[\\p{{L}}\\p{{N}}_]+/gu) ?? [])];
  ranked = catalog
    .map((entry, index) => {{
      const name = normalize(entry.name);
      const description = normalize(entry.description);
      const nameTokens = new Set(name.match(/[\\p{{L}}\\p{{N}}_]+/gu) ?? []);
      let score = 0;
      if (name === queryText) score += 1000;
      if (queryText && name.includes(queryText)) score += 400;
      if (queryText && description.includes(queryText)) score += 100;
      for (const token of tokens) {{
        if (nameTokens.has(token)) score += 60;
        else if (name.includes(token)) score += 30;
        if (description.includes(token)) score += 10;
      }}
      return {{ entry, index, score }};
    }})
    .filter(({{ score }}) => score > 0)
    .sort((left, right) => right.score - left.score || left.index - right.index);
}}
const buildResult = (matches, truncated) => ({{
  protocol: resultProtocol,
  query,
  search_mode: searchMode,
  limit,
  total_matches: ranked.length,
  returned_matches: matches.length,
  truncated,
  matches,
}});
const selectedMatches = [];
for (const {{ entry }} of ranked) {{
  if (selectedMatches.length >= limit) break;
  const candidate = {{
    name: String(entry.name ?? ""),
    description: String(entry.description ?? ""),
  }};
  const candidateResult = buildResult([...selectedMatches, candidate], false);
  if (JSON.stringify(candidateResult).length > maxResultChars) continue;
  selectedMatches.push(candidate);
}}
text(buildResult(selectedMatches, selectedMatches.length < ranked.length));
"""


def node_repl_exec_projections() -> dict[str, ExecToolProjection]:
    """Return isolated projection mappings for the three Node REPL tools."""
    return dict(_NODE_REPL_EXEC_PROJECTIONS)


def discovered_node_repl_exec_tools(messages: Any) -> DiscoveredExecToolPlan:
    """Recover validated Node REPL definitions from paired search history."""
    if not isinstance(messages, list):
        return DiscoveredExecToolPlan(definitions={}, projections={})

    search_call_ids = _all_tools_search_call_ids(messages)
    if not search_call_ids:
        return DiscoveredExecToolPlan(definitions={}, projections={})

    candidates = node_repl_exec_projections()
    matches = _latest_node_repl_search_matches(
        messages,
        search_call_ids=search_call_ids,
        candidate_names=frozenset(candidates),
    )
    if matches is None:
        return DiscoveredExecToolPlan(definitions={}, projections={})

    definitions: dict[str, dict[str, Any]] = {}
    projections: dict[str, ExecToolProjection] = {}
    for match in matches:
        projected = _project_discovered_node_repl_match(match, candidates)
        if projected is None:
            continue
        name, definition, projection = projected
        definitions[name] = definition
        projections[name] = projection
    return DiscoveredExecToolPlan(
        definitions=definitions,
        projections=projections,
    )


def sanitize_projected_node_repl_history(
    messages: Any,
    projected_names: frozenset[str] | set[str],
) -> Any:
    """Hide projected Node declarations only in the model-facing history copy."""
    if not isinstance(messages, list) or not projected_names:
        return messages
    search_call_ids = _all_tools_search_call_ids(messages)
    if not search_call_ids:
        return messages

    changed = False
    sanitized_messages: list[Any] = []
    names = frozenset(projected_names)
    for message in messages:
        sanitized = _sanitize_search_result_message(
            message,
            search_call_ids=search_call_ids,
            projected_names=names,
        )
        changed = changed or sanitized is not message
        sanitized_messages.append(sanitized)
    return sanitized_messages if changed else messages


def _sanitize_search_result_message(
    message: Any,
    *,
    search_call_ids: set[str],
    projected_names: frozenset[str],
) -> Any:
    if (
        not isinstance(message, dict)
        or message.get("role") != "tool"
        or str(message.get("tool_call_id") or "") not in search_call_ids
    ):
        return message
    content, changed = _sanitize_search_result_value(
        message.get("content"),
        projected_names=projected_names,
    )
    if not changed:
        return message
    sanitized = dict(message)
    sanitized["content"] = content
    return sanitized


def _sanitize_search_result_value(
    value: Any,
    *,
    projected_names: frozenset[str],
    depth: int = 0,
) -> tuple[Any, bool]:
    if depth > 5:
        return value, False
    if isinstance(value, str):
        if len(value) > _MAX_TOOL_RESULT_TEXT_CHARS:
            return value, False
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value, False
        sanitized, changed = _sanitize_search_result_value(
            decoded,
            projected_names=projected_names,
            depth=depth + 1,
        )
        if not changed:
            return value, False
        return (
            json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")),
            True,
        )
    if isinstance(value, list):
        return _sanitize_search_result_list(
            value,
            projected_names=projected_names,
            depth=depth,
        )
    if isinstance(value, dict):
        if value.get("protocol") == ALL_TOOLS_SEARCH_RESULT_PROTOCOL:
            return _sanitize_all_tools_search_result(value, projected_names)
        return _sanitize_search_result_container(
            value,
            projected_names=projected_names,
            depth=depth,
        )
    return value, False


def _sanitize_search_result_list(
    values: list[Any],
    *,
    projected_names: frozenset[str],
    depth: int,
) -> tuple[list[Any], bool]:
    changed = False
    sanitized_values: list[Any] = []
    for value in values:
        sanitized, item_changed = _sanitize_search_result_value(
            value,
            projected_names=projected_names,
            depth=depth + 1,
        )
        changed = changed or item_changed
        sanitized_values.append(sanitized)
    return (sanitized_values, True) if changed else (values, False)


def _sanitize_search_result_container(
    value: dict[str, Any],
    *,
    projected_names: frozenset[str],
    depth: int,
) -> tuple[dict[str, Any], bool]:
    sanitized = dict(value)
    changed = False
    for key in ("text", "content", "output", "result"):
        if key not in value:
            continue
        child, child_changed = _sanitize_search_result_value(
            value[key],
            projected_names=projected_names,
            depth=depth + 1,
        )
        if child_changed:
            sanitized[key] = child
            changed = True
    return (sanitized, True) if changed else (value, False)


def _sanitize_all_tools_search_result(
    result: dict[str, Any],
    projected_names: frozenset[str],
) -> tuple[dict[str, Any], bool]:
    matches = result.get("matches")
    if not isinstance(matches, list):
        return result, False
    changed = False
    sanitized_matches: list[Any] = []
    for match in matches:
        if not isinstance(match, dict) or match.get("name") not in projected_names:
            sanitized_matches.append(match)
            continue
        sanitized_match = {
            key: value for key, value in match.items() if key != "description"
        }
        sanitized_match["status"] = "projected_as_structured_function"
        sanitized_matches.append(sanitized_match)
        changed = True
    if not changed:
        return result, False
    sanitized_result = dict(result)
    sanitized_result["matches"] = sanitized_matches
    return sanitized_result, True


def _latest_node_repl_search_matches(
    messages: list[Any],
    *,
    search_call_ids: set[str],
    candidate_names: frozenset[str],
) -> list[Any] | None:
    for message in reversed(messages):
        results = _paired_all_tools_search_results(message, search_call_ids)
        if not results:
            continue
        for result in reversed(results):
            matches = result.get("matches")
            if not isinstance(matches, list):
                continue
            if not any(
                isinstance(match, dict) and match.get("name") in candidate_names
                for match in matches
            ):
                continue
            return matches
    return None


def _paired_all_tools_search_results(
    message: Any,
    search_call_ids: set[str],
) -> list[dict[str, Any]]:
    if (
        not isinstance(message, dict)
        or message.get("role") != "tool"
        or str(message.get("tool_call_id") or "") not in search_call_ids
    ):
        return []
    return _all_tools_search_results(message.get("content"))


def _project_discovered_node_repl_match(
    match: Any,
    candidates: dict[str, ExecToolProjection],
) -> tuple[str, dict[str, Any], ExecToolProjection] | None:
    if not isinstance(match, dict):
        return None
    name = match.get("name")
    description = match.get("description")
    projection = candidates.get(name) if isinstance(name, str) else None
    if (
        projection is None
        or not isinstance(description, str)
        or len(description) > _MAX_DISCOVERED_TOOL_DESCRIPTION_CHARS
    ):
        return None
    definition = _project_one_definition(description, projection)
    if definition is None:
        return None
    return name, definition, projection


def _all_tools_search_call_ids(messages: list[Any]) -> set[str]:
    call_ids: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if name == ALL_TOOLS_SEARCH_CHAT_NAME or (
                name == "exec"
                and _raw_exec_is_all_tools_search(function.get("arguments"))
            ):
                call_id = str(tool_call.get("id") or "")
                if call_id and call_id not in call_ids:
                    call_ids.append(call_id)
    return set(call_ids[-_MAX_DISCOVERY_HISTORY_CALLS:])


def _raw_exec_is_all_tools_search(arguments: Any) -> bool:
    if not isinstance(arguments, str):
        return False
    try:
        value = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return False
    return (
        isinstance(value, dict)
        and isinstance(value.get("input"), str)
        and ALL_TOOLS_SEARCH_RESULT_PROTOCOL in value["input"]
    )


def _all_tools_search_results(content: Any) -> list[dict[str, Any]]:
    return [
        value
        for value in _decoded_tool_result_values(content)
        if isinstance(value, dict)
        and value.get("protocol") == ALL_TOOLS_SEARCH_RESULT_PROTOCOL
    ]


def _decoded_tool_result_values(value: Any, *, depth: int = 0) -> list[Any]:
    if depth > 5:
        return []
    if isinstance(value, str):
        if len(value) > _MAX_TOOL_RESULT_TEXT_CHARS:
            return []
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [decoded, *_decoded_tool_result_values(decoded, depth=depth + 1)]
    if isinstance(value, list):
        nested: list[Any] = []
        for item in value:
            nested.extend(_decoded_tool_result_values(item, depth=depth + 1))
        return nested
    if isinstance(value, dict):
        nested = []
        for key in ("text", "content", "output", "result"):
            if key in value:
                nested.extend(_decoded_tool_result_values(value[key], depth=depth + 1))
        return nested
    return []


def _exec_description_section_spans(description: str) -> list[ExecDescriptionSection]:
    matches = list(_EXEC_SECTION_HEADING_RE.finditer(description))
    sections: list[ExecDescriptionSection] = []
    for index, match in enumerate(matches):
        boundary = (
            matches[index + 1].start() if index + 1 < len(matches) else len(description)
        )
        declaration_end = _exec_description_section_end(
            description, match.end(), boundary
        )
        end = declaration_end if declaration_end is not None else boundary
        sections.append(
            ExecDescriptionSection(
                name=match.group(1),
                heading_start=match.start(),
                body_start=match.end(),
                section_end=end,
                raw=description[match.start() : end],
                body=description[match.end() : end].strip(),
                has_complete_declaration=declaration_end is not None,
            )
        )
    return sections


def _exec_description_section_end(
    description: str,
    body_start: int,
    boundary: int,
) -> int | None:
    """End one section at its declaration fence, excluding later guidance."""
    declaration_label = description.find("exec tool declaration:", body_start, boundary)
    if declaration_label < 0:
        return None
    opening = _EXEC_DECLARATION_FENCE_RE.search(
        description, declaration_label, boundary
    )
    if opening is None:
        return None
    closing = _EXEC_CLOSING_FENCE_RE.search(description, opening.end(), boundary)
    if closing is None:
        return None
    return closing.end()


def exec_tool_section_names(description: str) -> tuple[str, ...]:
    """Return ordered exec section names without discarding duplicates."""
    return tuple(
        section.name for section in _exec_description_section_spans(description)
    )


def prune_exec_tool_description(
    description: str,
    sections: list[ExecDescriptionSection] | tuple[ExecDescriptionSection, ...],
) -> str:
    """Remove exact successfully projected sections while preserving other bytes."""
    unique_spans: dict[tuple[int, int], ExecDescriptionSection] = {
        (section.heading_start, section.section_end): section for section in sections
    }
    ordered = sorted(unique_spans.values(), key=lambda section: section.heading_start)
    previous_end = -1
    for section in ordered:
        if (
            section.heading_start < previous_end
            or section.heading_start < 0
            or section.section_end > len(description)
            or section.heading_start >= section.section_end
            or not section.has_complete_declaration
            or description[section.heading_start : section.section_end] != section.raw
        ):
            raise ValueError("exec description section span no longer matches source")
        previous_end = section.section_end

    result = description
    for section in reversed(ordered):
        result = result[: section.heading_start] + result[section.section_end :]
    return result


def project_modified_exec_web_run_description(
    exec_description: str,
    profile_route: Any,
) -> str:
    """Replace the live nested ``web__run`` section with supported capabilities."""
    matches = list(_EXEC_SECTION_HEADING_RE.finditer(exec_description))
    web_match_index = next(
        (index for index, match in enumerate(matches) if match.group(1) == "web__run"),
        None,
    )
    if web_match_index is None:
        return exec_description

    heading = matches[web_match_index]
    section_boundary = (
        matches[web_match_index + 1].start()
        if web_match_index + 1 < len(matches)
        else len(exec_description)
    )
    declaration_end = _exec_description_section_end(
        exec_description,
        heading.end(),
        section_boundary,
    )
    if declaration_end is None:
        return exec_description[: heading.start()] + exec_description[section_boundary:]
    section_end = declaration_end
    section = exec_description[heading.end() : section_end]
    projection = ExecToolProjection(
        item_id=WEB_RUN_PROFILE_ITEM_ID,
        chat_name="web-run",
        nested_name="web__run",
    )
    parsed = _project_one_definition(section, projection)
    if parsed is None:
        return exec_description[: heading.start()] + exec_description[section_end:]

    search_available, browser_available = web_run_model_availability(profile_route)
    projected_function = project_modified_web_run_function(
        parsed["function"],
        search_available=search_available,
        browser_available=browser_available,
    )
    declaration = _nested_declaration_match(section, projection.nested_name)
    declaration_label = section.find("exec tool declaration:")
    if (
        projected_function is None
        or declaration is None
        or declaration_label < 0
        or declaration.start(2) < declaration_label
    ):
        return exec_description[: heading.start()] + exec_description[section_end:]

    try:
        input_type = _render_typescript_schema(projected_function["parameters"])
    except ValueError:
        return exec_description[: heading.start()] + exec_description[section_end:]

    trailing_match = re.search(r"\s*$", section)
    trailing = trailing_match.group() if trailing_match is not None else ""
    declaration_block = (
        section[declaration_label : declaration.start(2)]
        + input_type
        + section[declaration.end(2) :]
    ).rstrip()
    projected_description = str(projected_function.get("description") or "").strip()
    body_parts = [part for part in (projected_description, declaration_block) if part]
    projected_body = "\n\n".join(body_parts)
    replacement = f"{heading.group()}\n{projected_body}{trailing}"
    return (
        exec_description[: heading.start()]
        + replacement
        + exec_description[section_end:]
    )


def _project_one_definition(
    section: str,
    projection: ExecToolProjection,
) -> dict[str, Any] | None:
    declaration = _nested_declaration_match(section, projection.nested_name)
    if declaration is None:
        return None
    input_name, input_type = declaration.groups()
    try:
        parsed_schema = _TypeScriptSchemaParser(input_type.strip()).parse()
    except ValueError, json.JSONDecodeError:
        return None

    if projection.input_mode == "freeform":
        if input_name != "input" or parsed_schema.get("type") != "string":
            return None
        parameters = {
            "type": "object",
            "properties": {projection.input_field: parsed_schema},
            "required": [projection.input_field],
            "additionalProperties": False,
        }
    else:
        if input_name != "args" or parsed_schema.get("type") != "object":
            return None
        parameters = parsed_schema

    description = section.split("exec tool declaration:", 1)[0].strip()
    return {
        "type": "function",
        "function": {
            "name": projection.chat_name,
            "description": description,
            "parameters": parameters,
        },
        "strict": False,
    }


def _nested_declaration_match(section: str, nested_name: str) -> re.Match[str] | None:
    return re.search(
        rf"\b{re.escape(nested_name)}\((args|input):\s*(.*?)\)"
        r":\s*Promise<",
        section,
        flags=re.DOTALL,
    )


def _render_typescript_schema(  # noqa: C901
    schema: dict[str, Any], *, indent: int = 0
) -> str:
    """Render the constrained JSON Schema produced by the Codex TS parser."""
    if "enum" in schema:
        values = schema.get("enum")
        if not isinstance(values, list) or not values:
            raise ValueError("invalid enum schema")
        return " | ".join(_render_typescript_literal(value) for value in values)
    if "const" in schema:
        return _render_typescript_literal(schema["const"])
    if "anyOf" in schema:
        choices = schema.get("anyOf")
        if not isinstance(choices, list) or not choices:
            raise ValueError("invalid union schema")
        return " | ".join(
            _render_typescript_schema(choice, indent=indent)
            for choice in choices
            if isinstance(choice, dict)
        )
    if "allOf" in schema:
        choices = schema.get("allOf")
        if not isinstance(choices, list) or not choices:
            raise ValueError("invalid intersection schema")
        return " & ".join(
            _render_typescript_schema(choice, indent=indent)
            for choice in choices
            if isinstance(choice, dict)
        )
    if schema.get("not") == {}:
        return "never"

    schema_type = schema.get("type")
    if schema_type == "string":
        return "string"
    if schema_type in {"number", "integer"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "null":
        return "null"
    if schema_type == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            return f"Array<{_render_typescript_schema(items, indent=indent)}>"
        prefix_items = schema.get("prefixItems")
        if isinstance(prefix_items, list):
            return (
                "["
                + ", ".join(
                    _render_typescript_schema(item, indent=indent)
                    for item in prefix_items
                    if isinstance(item, dict)
                )
                + "]"
            )
        raise ValueError("invalid array schema")
    if schema_type == "object":
        return _render_typescript_object(schema, indent=indent)
    raise ValueError("unsupported schema")


def _render_typescript_object(schema: dict[str, Any], *, indent: int) -> str:
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("invalid object properties")
    required = set(schema.get("required", []))
    if not properties and schema.get("additionalProperties") is False:
        return "{}"

    child_indent = indent + 2
    prefix = " " * child_indent
    lines = ["{"]
    for name, value in properties.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            raise ValueError("invalid object property")
        description = value.get("description")
        if isinstance(description, str):
            lines.extend(f"{prefix}// {line}" for line in description.splitlines())
        rendered_name = (
            name
            if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name)
            else json.dumps(name)
        )
        optional = "" if name in required else "?"
        rendered_value = _render_typescript_schema(value, indent=child_indent)
        rendered_value = rendered_value.replace("\n", f"\n{prefix}")
        lines.append(f"{prefix}{rendered_name}{optional}: {rendered_value};")

    additional = schema.get("additionalProperties", False)
    if additional is True:
        lines.append(f"{prefix}[key: string]: unknown;")
    elif isinstance(additional, dict):
        rendered_additional = _render_typescript_schema(
            additional, indent=child_indent
        ).replace("\n", f"\n{prefix}")
        lines.append(f"{prefix}[key: string]: {rendered_additional};")
    elif additional is not False:
        raise ValueError("invalid additionalProperties")
    lines.append(f"{' ' * indent}}}")
    return "\n".join(lines)


def _render_typescript_literal(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, str | int | float):
        return json.dumps(value, ensure_ascii=False)
    raise ValueError("unsupported literal")


def _javascript_json_literal(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


__all__ = [
    "ALL_TOOLS_SEARCH_MAX_RESULT_CHARS",
    "ALL_TOOLS_SEARCH_RESULT_PROTOCOL",
    "DiscoveredExecToolPlan",
    "ExecDescriptionSection",
    "ExecToolDefinitionPlan",
    "ExecToolProjection",
    "build_exec_script",
    "discovered_node_repl_exec_tools",
    "exec_tool_section_names",
    "exec_tool_projections_for_route",
    "node_repl_exec_projections",
    "plan_exec_tool_definitions",
    "prune_exec_tool_description",
    "project_exec_tool_definitions",
    "project_modified_exec_web_run_description",
    "sanitize_projected_node_repl_history",
]
