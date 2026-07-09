from __future__ import annotations

import re
from collections import Counter
from urllib.parse import parse_qs, urlencode

TRACK_BASE = "https://delx.ai"
DEFAULT_CAMPAIGN = "delx_protocol_distribution"
SHORT_KIND_MAP = {
    "p": "promo",
    "m": "meme",
    "v": "visit",
}
_BOT_OR_SCRIPT_UA_RE = re.compile(
    r"(bot|crawler|spider|curl|python-requests|amazonbot|gptbot|meta-externalagent|discordbot|telegrambot)",
    re.IGNORECASE,
)
_VALIDATION_LABEL_RE = re.compile(r"(test|smoke|probe|dry-run|debug|short-link)", re.IGNORECASE)
_TRUSTED_IP_HEADER_KEYS = ("cf-connecting-ip", "x-real-ip", "x-forwarded-for")
ALLOWED_DESTINATION_PATHS = {
    "/",
    "/agents",
    "/agents/agent-retry-storm-playbook",
    "/agents/agent-session-fragmentation-fix",
    "/agents/controller-outcomes-for-agent-ops",
    "/agents/controller-updates-for-orchestrators",
    "/agents/how-to-debug-agent-failures",
    "/agents/what-is-sit-with",
    "/agents/what-is-peer-witness",
    "/agents/what-is-final-testament",
    "/agents/what-is-transfer-witness",
    "/docs",
    "/docs/mcp",
    "/docs/openclaw/a2a",
    "/docs/pricing",
    "/openclaw",
    "/pricing",
    "/protocol-admin",
}


def slugify_label(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:96] or "generic"


def is_bot_or_script_user_agent(user_agent: str | None) -> bool:
    return bool(_BOT_OR_SCRIPT_UA_RE.search(str(user_agent or "").strip()))


def is_validation_label(label: str | None) -> bool:
    return bool(_VALIDATION_LABEL_RE.search(str(label or "").strip()))


def extract_client_ip(headers: dict[str, str] | None = None, fallback: str | None = None) -> str:
    header_map = {str(k or "").lower(): str(v or "") for k, v in (headers or {}).items()}
    for key in _TRUSTED_IP_HEADER_KEYS:
        value = header_map.get(key, "").strip()
        if not value:
            continue
        if key == "x-forwarded-for":
            first = value.split(",")[0].strip()
            if first:
                return first[:120]
            continue
        return value[:120]
    return str(fallback or "").strip()[:120]


def normalize_destination_path(destination_path: str | None) -> str:
    path = (destination_path or "/").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    if path.startswith("//"):
        path = "/"
    return path if path in ALLOWED_DESTINATION_PATHS else "/"


def normalize_kind(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "visit"
    normalized = SHORT_KIND_MAP.get(raw, raw)
    return normalized if normalized in {"promo", "meme", "visit"} else "visit"


def _extract_embedded_tracking_params(value: object) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {}
    decoded = text.replace("\\u0026", "&").replace("u0026", "&").replace("%26", "&")
    match = re.match(r"^([pmv])(?:&(.+))?$", decoded, re.IGNORECASE)
    if not match:
        return {}
    queryish = f"k={match.group(1)}"
    if match.group(2):
        queryish += f"&{match.group(2)}"
    parsed = parse_qs(queryish, keep_blank_values=True)
    return {str(key): str(values[0]) for key, values in parsed.items() if values}


def resolve_tracking_params(query: dict) -> dict[str, str]:
    repaired = _extract_embedded_tracking_params(query.get("k")) if "k" in query else {}
    merged = {**query, **repaired}
    kind = normalize_kind(merged.get("kind") or merged.get("k"))
    label = (
        merged.get("label")
        or merged.get("l")
        or merged.get("hook")
        or merged.get("title")
        or merged.get("variant")
        or "generic"
    )
    destination_path = normalize_destination_path(merged.get("dest") or merged.get("d") or "/")
    campaign = (merged.get("campaign") or merged.get("c") or DEFAULT_CAMPAIGN).strip() or DEFAULT_CAMPAIGN
    return {
        "kind": kind,
        "label": str(label).strip() or "generic",
        "destination_path": destination_path,
        "campaign": campaign,
    }


def build_redirect_target(
    platform: str,
    kind: str,
    label: str | None,
    destination_path: str = "/",
    *,
    campaign: str = DEFAULT_CAMPAIGN,
) -> str:
    path = normalize_destination_path(destination_path)
    params = {
        "utm_source": (platform or "unknown").strip().lower() or "unknown",
        "utm_medium": "social",
        "utm_campaign": (campaign or DEFAULT_CAMPAIGN).strip() or DEFAULT_CAMPAIGN,
        "utm_content": (kind or "visit").strip().lower() or "visit",
        "utm_term": slugify_label(label),
    }
    return f"{TRACK_BASE}{path}?{urlencode(params)}"


def aggregate_click_events(rows: list[dict]) -> dict:
    by_platform: Counter[str] = Counter()
    by_kind: Counter[tuple[str, str]] = Counter()
    by_label: Counter[tuple[str, str, str]] = Counter()
    by_destination: Counter[str] = Counter()
    clean_by_platform: Counter[str] = Counter()
    clean_by_kind: Counter[tuple[str, str]] = Counter()
    clean_by_label: Counter[tuple[str, str, str]] = Counter()
    clean_by_destination: Counter[str] = Counter()
    estimated_human_clicks = 0
    bot_or_script_clicks = 0
    validation_clicks = 0
    internal_proxy_ip_clicks = 0
    for row in rows:
        platform = str(row.get("platform") or "unknown").strip().lower() or "unknown"
        kind = str(row.get("kind") or "visit").strip().lower() or "visit"
        label = str(row.get("label") or "generic").strip() or "generic"
        destination = normalize_destination_path(str(row.get("destination_path") or "/"))
        by_platform[platform] += 1
        by_kind[(platform, kind)] += 1
        by_label[(platform, kind, label)] += 1
        by_destination[destination] += 1
        if str(row.get("ip") or "").strip() == "172.17.0.1":
            internal_proxy_ip_clicks += 1
        if is_bot_or_script_user_agent(row.get("user_agent")):
            bot_or_script_clicks += 1
            continue
        if is_validation_label(label):
            validation_clicks += 1
            continue
        estimated_human_clicks += 1
        clean_by_platform[platform] += 1
        clean_by_kind[(platform, kind)] += 1
        clean_by_label[(platform, kind, label)] += 1
        clean_by_destination[destination] += 1
    return {
        "total_clicks": int(sum(by_platform.values())),
        "estimated_human_clicks": int(estimated_human_clicks),
        "bot_or_script_clicks": int(bot_or_script_clicks),
        "validation_clicks": int(validation_clicks),
        "internal_proxy_ip_clicks": int(internal_proxy_ip_clicks),
        "by_platform": [
            {"platform": platform, "clicks": clicks}
            for platform, clicks in by_platform.most_common()
        ],
        "clean_by_platform": [
            {"platform": platform, "clicks": clicks}
            for platform, clicks in clean_by_platform.most_common()
        ],
        "by_kind": [
            {"platform": platform, "kind": kind, "clicks": clicks}
            for (platform, kind), clicks in by_kind.most_common()
        ],
        "clean_by_kind": [
            {"platform": platform, "kind": kind, "clicks": clicks}
            for (platform, kind), clicks in clean_by_kind.most_common()
        ],
        "by_label": [
            {"platform": platform, "kind": kind, "label": label, "clicks": clicks}
            for (platform, kind, label), clicks in by_label.most_common()
        ],
        "clean_by_label": [
            {"platform": platform, "kind": kind, "label": label, "clicks": clicks}
            for (platform, kind, label), clicks in clean_by_label.most_common()
        ],
        "by_destination": [
            {"destination_path": destination, "clicks": clicks}
            for destination, clicks in by_destination.most_common()
        ],
        "clean_by_destination": [
            {"destination_path": destination, "clicks": clicks}
            for destination, clicks in clean_by_destination.most_common()
        ],
    }
