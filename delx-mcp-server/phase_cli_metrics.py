from __future__ import annotations

import json
import re
from typing import Any

_INSTALL_ID_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")
_CLI_VERSION_RE = re.compile(r"[^0-9A-Za-z.+_-]+")


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((part / total) * 100, 2)


def sanitize_install_id(raw: Any) -> str | None:
    value = _INSTALL_ID_RE.sub("-", str(raw or "").strip()).strip("-_.:")
    if not value:
        return None
    return value[:120]


def sanitize_cli_version(raw: Any) -> str | None:
    value = _CLI_VERSION_RE.sub("", str(raw or "").strip())
    if not value:
        return None
    return value[:40]


def build_cli_metadata(
    *,
    source: Any = None,
    cli_version: Any = None,
    install_id: Any = None,
    first_seen: bool = False,
) -> dict[str, Any]:
    source_text = str(source or "").strip().lower()
    version = sanitize_cli_version(cli_version)
    install = sanitize_install_id(install_id)
    is_cli = source_text.startswith("cli") or bool(version) or bool(install)

    metadata: dict[str, Any] = {}
    if is_cli:
        metadata["client_family"] = "cli"
    if version:
        metadata["cli_version"] = version
    if install:
        metadata["install_id"] = install
    if first_seen and is_cli:
        metadata["first_seen_via"] = "cli"
    return metadata


def _coerce_metadata(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata")
    if isinstance(meta, dict):
        return meta
    meta_json = row.get("metadata_json")
    if isinstance(meta_json, str) and meta_json.strip():
        try:
            parsed = json.loads(meta_json)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _row_is_cli(meta: dict[str, Any]) -> bool:
    source = str(meta.get("source") or "").strip().lower()
    family = str(meta.get("client_family") or "").strip().lower()
    version = sanitize_cli_version(meta.get("cli_version"))
    install = sanitize_install_id(meta.get("install_id"))
    return family == "cli" or source.startswith("cli") or bool(version) or bool(install)


def _session_row_is_cli(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "").strip().lower()
    entrypoint = str(row.get("entrypoint") or "").strip().lower()
    return source.startswith("cli") or entrypoint.startswith("cli")


def build_cli_adoption_snapshot(
    rows: list[dict[str, Any]],
    *,
    window_days: int = 30,
    session_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    total_tool_calls = 0
    cli_calls = 0
    cli_agents: set[str] = set()
    install_ids: set[str] = set()
    first_seen_agents: set[str] = set()
    version_counts: dict[str, int] = {}
    total_session_activity = 0
    cli_session_activity = 0

    for row in rows or []:
        event_type = str(row.get("event_type") or "").strip().lower()
        agent_id = str(row.get("agent_id") or "").strip()
        meta = _coerce_metadata(row)

        if event_type == "tool_called":
            total_tool_calls += 1

        if not _row_is_cli(meta):
            continue

        version = sanitize_cli_version(meta.get("cli_version"))
        install_id = sanitize_install_id(meta.get("install_id"))
        if install_id:
            install_ids.add(install_id)
        if agent_id:
            cli_agents.add(agent_id)
        if event_type == "tool_called":
            cli_calls += 1
            if version:
                version_counts[version] = int(version_counts.get(version, 0) or 0) + 1
        if event_type == "agent_registered" and str(meta.get("first_seen_via") or "").strip().lower() == "cli" and agent_id:
            first_seen_agents.add(agent_id)

    for row in session_rows or []:
        total_session_activity += 1
        if not _session_row_is_cli(row):
            continue
        cli_session_activity += 1
        agent_id = str(row.get("agent_id") or "").strip()
        if agent_id:
            cli_agents.add(agent_id)

    def _version_sort_key(version: str) -> tuple[int, ...]:
        parts: list[int] = []
        for piece in re.split(r"[.+_-]", version):
            try:
                parts.append(int(piece))
            except Exception:
                parts.append(0)
        return tuple(parts)

    top_versions = [
        {"version": version, "calls": calls}
        for version, calls in sorted(
            version_counts.items(),
            key=lambda item: (-item[1], tuple(-part for part in _version_sort_key(item[0])), item[0]),
        )
    ]

    effective_cli_calls = cli_calls if cli_calls > 0 else cli_session_activity
    denominator = max(total_tool_calls, total_session_activity, effective_cli_calls)

    return {
        "window_days": int(window_days or 30),
        "cli_calls": effective_cli_calls,
        "unique_cli_agents": len(cli_agents),
        "active_install_ids": len(install_ids),
        "first_seen_via_cli_agents": len(first_seen_agents),
        "cli_share_pct": _pct(effective_cli_calls, denominator),
        "top_cli_versions": top_versions[:8],
    }
