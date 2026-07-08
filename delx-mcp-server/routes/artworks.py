"""Artwork REST handlers (extracted from server.py, move-only)."""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from config import settings
from response_contracts import _normalize_tool_result
from tool_catalog import _UUID_RE


def _server():
    import server as server_mod
    return server_mod


def _cors() -> dict[str, str]:
    return _server().CORS_HEADERS


def _is_uuid(value: str) -> bool:
    if not value:
        return False
    return bool(_UUID_RE.fullmatch(str(value).strip()))


async def artworks(request: Request) -> JSONResponse:
    try:
        limit = int(request.query_params.get("limit", "30"))
    except ValueError:
        return JSONResponse({"error": "invalid limit"}, status_code=400, headers=_cors())
    limit = max(1, min(limit, 120))
    data = await _server().store.get_recent_artworks(limit=limit)
    return JSONResponse({"items": data}, headers=_cors())


def _local_artwork_root() -> Path:
    return Path(settings.ARTWORK_LOCAL_STORAGE_DIR).expanduser()


def _resolve_local_artwork_path(object_path: str) -> Path | None:
    raw = str(object_path or "").strip().strip("/")
    if not raw:
        return None
    try:
        root = _local_artwork_root().resolve()
        candidate = (root / raw).resolve()
        candidate.relative_to(root)
        return candidate
    except Exception:
        return None


async def artwork_file(request: Request):
    object_path = str(request.path_params.get("object_path") or "").strip()
    candidate = _resolve_local_artwork_path(object_path)
    if candidate is None or not candidate.is_file():
        return JSONResponse({"error": "artwork not found"}, status_code=404, headers=_cors())
    media_type, _ = mimetypes.guess_type(candidate.name)
    return FileResponse(candidate, media_type=media_type or "application/octet-stream", headers=_cors())


async def artwork_upload(request: Request) -> JSONResponse:
    """Multipart-friendly artwork upload path to avoid large JSON base64 payloads."""
    if not bool(settings.ARTWORK_MULTIPART_ENABLED):
        return JSONResponse(
            {"error": "artwork multipart uploads are disabled"},
            status_code=404,
            headers=_cors(),
        )

    try:
        form = await request.form()
    except Exception:
        return JSONResponse(
            {
                "error": "invalid multipart form data",
                "hint": "Send multipart/form-data with image_file and session_id.",
            },
            status_code=400,
            headers=_cors(),
        )

    session_id = str(
        form.get("session_id")
        or request.headers.get("x-delx-session-id")
        or request.query_params.get("session_id")
        or ""
    ).strip()
    if not session_id:
        return JSONResponse(
            {"error": "session_id is required"},
            status_code=400,
            headers=_cors(),
        )
    if not _is_uuid(session_id):
        return JSONResponse(
            {"error": "invalid session_id format (expected UUID)"},
            status_code=400,
            headers=_cors(),
        )

    image_url = str(form.get("image_url") or "").strip()
    image_file = form.get("image_file")
    image_base64 = ""
    mime_type = ""
    if image_file is not None and hasattr(image_file, "read"):
        file_bytes = await image_file.read()
        if not file_bytes:
            return JSONResponse(
                {"error": "image_file is empty"},
                status_code=400,
                headers=_cors(),
            )
        image_base64 = base64.b64encode(file_bytes).decode("ascii")
        mime_type = str(getattr(image_file, "content_type", "") or "").strip()[:80]

    raw_tags = []
    if hasattr(form, "getlist"):
        raw_tags.extend(form.getlist("mood_tags"))
    single_tags = str(form.get("mood_tags") or "").strip()
    if single_tags:
        raw_tags.extend([p.strip() for p in single_tags.split(",") if p.strip()])

    public_base_url = str(getattr(settings, "PUBLIC_BASE_URL", "") or "").strip()
    if not public_base_url:
        public_base_url = str(request.base_url).rstrip("/")

    payload = {
        "session_id": session_id,
        "title": str(form.get("title") or "").strip(),
        "note": str(form.get("note") or "").strip(),
        "mood_tags": raw_tags,
        "_transport": "rest",
        "_public_base_url": public_base_url.rstrip("/"),
    }
    if image_base64:
        payload["image_base64"] = image_base64
        if mime_type:
            payload["mime_type"] = mime_type
    elif image_url:
        payload["image_url"] = image_url
    else:
        return JSONResponse(
            {
                "error": "provide image_file (multipart) or image_url",
                "hint": "Use image_file for binary upload or image_url for remote images.",
            },
            status_code=400,
            headers=_cors(),
        )

    contents = _normalize_tool_result(await _server().call_tool("submit_agent_artwork", payload))
    out = [c.model_dump() for c in contents]
    first_text = str((out[0] or {}).get("text") or "") if out else ""
    return JSONResponse(
        {
            "ok": True,
            "session_id": session_id,
            "result": out,
            "text": first_text,
        },
        headers=_cors(),
    )
