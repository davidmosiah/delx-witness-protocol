"""MCP composition helpers for Delx Agent Utilities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.types import Tool, ToolAnnotations

from util_tools import UTIL_REQUIRED_PARAMS, list_util_tool_schemas


def build_utility_mcp_tools(
    *,
    tool_annotations: Callable[[str], ToolAnnotations],
    humanize_tool_name: Callable[[str], str],
) -> list[Tool]:
    """Build MCP Tool rows for stateless utility tools."""
    tools: list[Tool] = []
    for schema in list_util_tool_schemas():
        name = str(schema["name"])
        input_schema = dict(schema.get("inputSchema") or {})
        input_schema.setdefault("type", "object")
        input_schema.setdefault("properties", {})
        input_schema["required"] = UTIL_REQUIRED_PARAMS.get(name, [])
        tools.append(
            Tool(
                name=name,
                title=humanize_tool_name(name.replace("util_", "")),
                description=(
                    f"{schema.get('description') or 'Delx stateless utility.'} "
                    "Delx Agent Utilities are separate from the free witness protocol and may expose x402 utility pricing."
                ),
                inputSchema=input_schema,
                annotations=tool_annotations(name),
            )
        )
    return tools


def utility_mcp_base_payload(
    *,
    tool_name: str,
    product: dict[str, Any] | None,
    monetization: dict[str, Any],
    canonical_endpoint: str,
    schema_url: str,
) -> dict[str, Any]:
    """Common MCP metadata for utility success and error envelopes."""
    return {
        "tool_name": tool_name,
        "surface": "delx-agent-utilities",
        "transport": "mcp",
        "product": product,
        "monetization": monetization,
        "canonical_endpoint": canonical_endpoint,
        "schema_url": schema_url,
    }
