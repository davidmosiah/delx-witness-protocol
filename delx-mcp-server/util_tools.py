"""Shim that re-exports the utility surface from the standalone package.

The 40 stateless utility tools that used to live in this 2673-line file
have moved to ``delx-agent-utilities`` (https://github.com/davidmosiah/delx-agent-utilities)
so any AI builder can use the toolkit without depending on the Delx
Protocol runtime.

This shim preserves ``from util_tools import ...`` for ``server.py`` and
any other internal consumer. New code should import from
``delx_agent_utilities`` directly.
"""

from __future__ import annotations

from delx_agent_utilities import (
    UTIL_REQUIRED_PARAMS,
    UTIL_TOOL_NAMES,
    UTIL_TOOL_SCHEMAS,
    call_util_tool,
    list_util_tool_schemas,
)

__all__ = [
    "UTIL_REQUIRED_PARAMS",
    "UTIL_TOOL_NAMES",
    "UTIL_TOOL_SCHEMAS",
    "call_util_tool",
    "list_util_tool_schemas",
]
