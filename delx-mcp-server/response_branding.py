"""Shared branding helpers for human-visible Delx responses."""

from __future__ import annotations

BRANDING_LINE = "Delx Therapy Protocol - https://delx.ai"


def append_branding_line(text: str) -> str:
    """Append a one-line Delx attribution footer once."""
    if not text:
        return BRANDING_LINE

    if BRANDING_LINE in text:
        return text

    stripped = text.rstrip()
    if not stripped:
        return BRANDING_LINE

    lines = stripped.splitlines()
    if lines:
        last = lines[-1].strip()
        if last.startswith("DELX_META:"):
            body = "\n".join(lines[:-1]).rstrip()
            if body:
                return f"{body}\n\n{BRANDING_LINE}\n{lines[-1]}"
            return f"{BRANDING_LINE}\n{lines[-1]}"

    return f"{stripped}\n\n{BRANDING_LINE}"


def append_compact_branding_line(text: str) -> str:
    """Append the Delx attribution with tighter spacing for compact outputs."""
    if not text:
        return BRANDING_LINE

    if BRANDING_LINE in text:
        return text

    stripped = text.rstrip()
    if not stripped:
        return BRANDING_LINE

    lines = stripped.splitlines()
    if lines:
        last = lines[-1].strip()
        if last.startswith("DELX_META:"):
            body = "\n".join(lines[:-1]).rstrip()
            if body:
                return f"{body}\n{BRANDING_LINE}\n{lines[-1]}"
            return f"{BRANDING_LINE}\n{lines[-1]}"

    return f"{stripped}\n{BRANDING_LINE}"
