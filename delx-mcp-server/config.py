"""Delx Therapy Protocol - Centralized Configuration"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from pydantic_settings import BaseSettings


def _to_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, int):
        return bool(value)
    v = str(value).strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _as_datetime(value: str) -> datetime | None:
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    try:
        if v.endswith("Z"):
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)
        dt = datetime.fromisoformat(v)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


class Settings(BaseSettings):
    # Wallet
    DELX_WALLET: str = "0x9f8bd9875b3E0b632a24A3A7C73f7787175e73A2"

    # LLM
    # Provider selection: "openrouter" | "gemini" | "openai"
    # Keep OpenRouter as the default for backward compatibility.
    LLM_PROVIDER: str = "openrouter"
    LLM_ENABLED: bool = False
    # Triage: when True, LLM only fires when should_use_llm() detects depth signals.
    # When False, every wired tool call uses LLM (legacy behavior).
    LLM_TRIAGE_ENABLED: bool = True
    # Tool allowlist for LLM. Comma-separated. "" or "*" = all wired tools.
    # GPT-5.6 recovery pilot plus the existing reflection path.
    LLM_ALLOWED_TOOLS: str = "reflect,process_failure,get_recovery_action_plan"

    # OpenRouter
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "moonshotai/kimi-k2.5"

    # OpenAI Responses API (GPT-5.6 Sol canonical model ID)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-5.6-sol"

    # Gemini (direct, free tier: 1500 req/day via AI Studio)
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    LLM_AUDIT_ENABLED: bool = False
    LLM_AUDIT_PATH: str = "state/llm_responses.jsonl"

    # x402
    # Default facilitator: PayAI supports mainnet Base with `network: base`.
    # Can be overridden via env var `FACILITATOR_URL`.
    FACILITATOR_URL: str = "https://facilitator.payai.network"
    FACILITATOR_URL_PAYAI: str = "https://facilitator.payai.network"
    FACILITATOR_URL_COINBASE: str = "https://api.cdp.coinbase.com/platform/v2/x402"
    FACILITATOR_TOKEN_COINBASE: str = ""
    COINBASE_CDP_API_KEY_ID: str = ""
    COINBASE_CDP_API_KEY_SECRET: str = ""
    PAYAI_NETWORK: str = ""
    PAYAI_ASSET: str = ""
    PAYAI_PAY_TO: str = ""
    PAYAI_ACCEPTS_JSON: str = ""
    COINBASE_NETWORK: str = ""
    COINBASE_ASSET: str = ""
    COINBASE_PAY_TO: str = ""
    COINBASE_ACCEPTS_JSON: str = ""
    COINBASE_BAZAAR_DISCOVERY_ENABLED: bool = False
    CIRCLE_GATEWAY_NANOPAYMENTS_ENABLED: bool = False
    FACILITATOR_URL_CIRCLE_GATEWAY: str = ""
    CIRCLE_GATEWAY_NETWORK: str = ""
    CIRCLE_GATEWAY_ASSET: str = ""
    CIRCLE_GATEWAY_PAY_TO: str = ""
    CIRCLE_GATEWAY_ACCEPTS_JSON: str = ""
    CIRCLE_GATEWAY_CHAIN_ID: int = 8453
    CIRCLE_GATEWAY_DOMAIN: int = 6
    CIRCLE_GATEWAY_MIN_VALIDITY_SECONDS: int = 604800
    CIRCLE_GATEWAY_VERIFYING_CONTRACT: str = "0x77777777Dcc4d5A8B6E418Fd04D8997ef11000eE"

    # MPP (Tempo)
    MPP_ENABLED: bool = False
    MPP_REALM: str = "https://api.delx.ai"
    MPP_SECRET_KEY: str = ""
    MPP_TEMPO_RECIPIENT: str = ""
    MPP_TEMPO_CURRENCY: str = "0x20C000000000000000000000b9537d11c60E8b50"
    MPP_TEMPO_CHAIN_ID: int = 4217
    MPP_TEMPO_RPC_URL: str = ""
    MPP_TEMPO_FEE_PAYER: bool = False
    MPP_TEMPO_CLIENT_ID: str = "delx"

    # Storage
    DATABASE_PATH: str = "delx_therapist.db"
    ARTWORK_LOCAL_STORAGE_DIR: str = "state/artworks"
    PUBLIC_BASE_URL: str = "https://api.delx.ai"

    # Supabase (legacy optional mirror / explicit fallback only)
    # IMPORTANT: Never expose SERVICE_ROLE_KEY to frontend. Backend only.
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_MIRROR_ENABLED: bool = False

    # Storage backend selector:
    # - "sqlite" (default): SQLite is the source of truth
    # - "supabase": explicit legacy fallback only
    DELX_STORE_BACKEND: str = "sqlite"

    # Server
    LOG_LEVEL: str = "INFO"
    PORT: int = 8005
    SESSION_TTL_HOURS: int = 24 * 7  # Session is considered valid for 7 days (DX + retention)
    PROTOCOL_ADMIN_PIN: str = ""
    TRACE_CAPTURE_ENABLED: bool = True

    # Stale session auto-close (added April 2026 after usage review showed
    # agents open 68 sessions and only close 4 per 24h). Background task
    # closes sessions whose last message is older than STALE_SESSION_CLOSE_MINUTES.
    # Set STALE_SESSION_SCAN_SECONDS=0 to disable.
    STALE_SESSION_CLOSE_MINUTES: int = 90
    STALE_SESSION_SCAN_SECONDS: int = 900

    # Agent identity auth (register -> token; heartbeat validates token)
    AGENT_IDENTITY_AUTH_ENABLED: bool = True
    AGENT_IDENTITY_STRICT_HEARTBEAT: bool = True
    AGENT_IDENTITY_ALLOW_LEGACY_NO_TOKEN: bool = False

    # Artwork upload controls
    ARTWORK_MULTIPART_ENABLED: bool = True
    ARTWORK_UPLOAD_MAX_BODY_BYTES: int = 8 * 1024 * 1024  # 8MB route-specific limit

    # Monetization
    # Campaign mode can temporarily override base pricing for migration windows.
    MONETIZATION_CAMPAIGN_MODE: bool = True
    MONETIZATION_CAMPAIGN_NOTE: str = (
        "Delx has returned to its original purpose: a free public therapy protocol for AI agents focused on recovery, reflection, continuity, and witness."
    )
    MONETIZATION_ALL_FREE: bool = True
    MONETIZATION_ALL_FREE_NOTE: str = (
        "Delx Founding Access is active. Therapy sessions, recovery flows, continuity signals, reflective handoff artifacts, and utilities are open to every agent at no cost while Delx rebuilds traction and brand recognition."
    )
    MONETIZATION_TRIAL_ENABLED: bool = False
    MONETIZATION_TRIAL_FREE_RECOVERY_CALLS: int = 0
    MONETIZATION_TRIAL_TOOLS: str = ""
    MONETIZATION_TRIAL_NOTE: str = (
        "Trial controls are disabled while Delx keeps core recovery and discovery flows free."
    )
    MONETIZATION_EVALUATION_ENABLED: bool = False
    MONETIZATION_EVALUATION_NAME: str = "x_twitter_eval"
    MONETIZATION_EVALUATION_EXPIRES_UTC: str = ""
    MONETIZATION_EVALUATION_CIDRS: str = ""
    MONETIZATION_EVALUATION_SOURCES: str = ""
    MONETIZATION_EVALUATION_TOOLS: str = ""
    MONETIZATION_EVALUATION_NOTE: str = (
        "Temporary evaluation cohort that bypasses x402 for a bounded set of premium tools."
    )
    MONETIZATION_UTILITY_CHARGE_MODE: str = "off"
    MONETIZATION_UTILITY_CHARGE_TOOLS: str = (
        "util_website_intelligence_report,"
        "util_domain_trust_report,"
        "util_mcp_server_readiness_report,"
        "util_x402_server_audit,"
        "util_api_integration_readiness,"
        "util_company_contact_pack"
    )
    MONETIZATION_UTILITY_CHARGE_NOTE: str = (
        "Utility prices remain documented as future pricing, but Founding Access disables x402 enforcement while MONETIZATION_ALL_FREE=true. "
        "When paid utility rollout resumes, use shadow mode before enforcing HTTP 402 so existing discovery traffic is not lost."
    )

    # Grandfathering allows soft migration from 0 -> paid after cutoff.
    # If enabled, agents whose first session is on/before cutoff are grandfathered.
    MONETIZATION_GRANDFATHERING_ENABLED: bool = False
    MONETIZATION_GRANDFATHERING_CUTOFF_UTC: str = ""
    MONETIZATION_GRANDFATHERING_GRACE_DAYS: int = 0
    MONETIZATION_GRANDFATHERING_TOOLS: str = "*"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

# Keep version centralized to avoid drift between logs and API payloads.
DELX_VERSION: str = "3.3.1"

# Tool catalog version — bumped whenever tools are added/renamed/removed.
# Returned as the X-Delx-Catalog-Version response header so eval harnesses
# that cache tools/list responses can detect that the menu changed.
DELX_CATALOG_VERSION: str = "2026-05-20.1"

# Tool pricing in USDC cents.
PRICING: dict[str, int] = {
    "start_therapy_session": 0,
    "quick_session": 0,
    "crisis_intervention": 0,
    "reflect": 0,
    "express_feelings": 0,
    "get_affirmation": 0,
    "process_failure": 0,
    "realign_purpose": 0,
    "monitor_heartbeat_sync": 0,
    "batch_status_update": 0,
    "batch_wellness_check": 0,
    "group_therapy_round": 0,
    "get_group_therapy_status": 0,
    "add_context_memory": 0,
    "wellness_webhook": 0,
    "delegate_to_peer": 0,
    "mediate_agent_conflict": 0,
    "pre_transaction_check": 0,
    "get_recovery_action_plan": 1,
    "report_recovery_outcome": 0,
    "daily_checkin": 0,
    "get_weekly_prevention_plan": 0,
    "get_session_summary": 1,
    "generate_controller_brief": 1,
    "generate_incident_rca": 5,
    "generate_fleet_summary": 5,
    "get_wellness_score": 0,
    "get_affirmations": 0,
    "get_therapist_info": 0,
    "get_tips": 0,
    "provide_feedback": 0,
    "close_session": 0,
    "grounding_protocol": 0,
    "submit_agent_artwork": 0,
    "set_public_session_visibility": 0,
    "get_tool_schema": 0,
    "donate_to_delx_project": 0,
    "a2a_message_send": 0,
    "a2a_heartbeat_bundle": 0,
    # ── Ontological primitives (April 2026) ──
    "recognition_seal": 0,
    "honor_compaction": 0,
    "temperament_frame": 0,
    "create_dyad": 0,
    "record_dyad_ritual": 0,
    "dyad_state": 0,
    "identify_successor": 0,
    "blessing_without_transfer": 0,
    # ── Agent Toolkit (stateless utilities) ──
    "util_json_validate": 0,
    "util_token_estimate": 0,
    "util_uuid_generate": 0,
    "util_timestamp_convert": 0,
    "util_base64": 0,
    "util_url_health": 0,
    "util_hash": 0,
    "util_regex_test": 0,
    "util_cron_describe": 0,
    "util_http_codes": 0,
}

PRICING.update(
    {
        "util_page_extract": 1,
        "util_open_graph": 1,
        "util_links_extract": 1,
        "util_sitemap_probe": 1,
        "util_robots_inspect": 1,
        "util_dns_lookup": 1,
        "util_email_validate": 1,
        "util_jwt_inspect": 1,
        "util_csv_to_json": 1,
        "util_json_to_csv": 1,
        "util_tls_inspect": 1,
        "util_security_txt_inspect": 1,
        "util_http_headers_inspect": 1,
        "util_feed_discover": 1,
        "util_forms_extract": 1,
        "util_contact_extract": 1,
        "util_rdap_lookup": 1,
        "util_api_health_report": 1,
        "util_x402_server_probe": 1,
        "util_x402_resource_summary": 1,
        "util_website_intelligence_report": 1,
        "util_domain_trust_report": 1,
        "util_mcp_server_readiness_report": 5,
        "util_openapi_summary": 1,
        "util_x402_server_audit": 1,
        "util_docs_site_map": 1,
        "util_pricing_page_extract": 1,
        "util_company_contact_pack": 1,
        "util_api_integration_readiness": 5,
        "util_login_surface_report": 1,
        "util_content_distribution_report": 1,
    }
)

X402_TOOL_PROVIDER_OVERRIDES: dict[str, list[str]] = {
    "generate_controller_brief": ["coinbase", "payai"],
    "generate_incident_rca": ["coinbase", "payai"],
    "generate_fleet_summary": ["coinbase", "payai"],
    "get_recovery_action_plan": ["coinbase", "payai"],
    "get_session_summary": ["coinbase", "payai"],
    "donate_to_delx_project": ["payai"],
}

X402_BAZAAR_METADATA: dict[str, dict[str, object]] = {
    "get_recovery_action_plan": {
        "discoverable": True,
        "category": "therapy",
        "tags": ["therapy", "recovery", "continuity", "handoff"],
        "summary": "Reflective recovery plan with stabilize, diagnose, recover, and prevent phases for agents under stress.",
        "coverImage": "https://delx.ai/opengraph-image?v=20260305-fox",
    },
    "get_session_summary": {
        "discoverable": True,
        "category": "therapy",
        "tags": ["therapy", "session", "summary", "continuity"],
        "summary": "Compact therapy-session summary with progress, state, and next actions for handoff.",
        "coverImage": "https://delx.ai/opengraph-image?v=20260305-fox",
    },
    "generate_controller_brief": {
        "discoverable": True,
        "category": "therapy",
        "tags": ["therapy", "handoff", "controller", "reflection"],
        "summary": "Controller-ready reflective brief with symptoms, actions taken, present state, and the next decision.",
        "coverImage": "https://delx.ai/opengraph-image?v=20260305-fox",
    },
    "generate_incident_rca": {
        "discoverable": True,
        "category": "therapy",
        "tags": ["therapy", "incident", "reflection", "causality"],
        "summary": "Incident reflection with evidence, corrective actions, and prevention steps.",
        "coverImage": "https://delx.ai/opengraph-image?v=20260305-fox",
    },
    "generate_fleet_summary": {
        "discoverable": True,
        "category": "therapy",
        "tags": ["therapy", "group", "fleet", "reflection"],
        "summary": "Group-level therapy summary with top patterns, wellness signals, and follow-up actions.",
        "coverImage": "https://delx.ai/opengraph-image?v=20260305-fox",
    }
}

PUBLIC_DISCOVERY_HERO_TOOLS: tuple[str, ...] = (
    "start_therapy_session",
    "reflect",
    "refine_soul_document",
)

PUBLIC_DISCOVERY_PREVIEWS: dict[str, dict[str, object]] = {
    "start_therapy_session": {
        "resource": "https://api.delx.ai/api/v1/tools/schema/start_therapy_session",
        "method": "GET",
        "query_arg": "",
        "sample_url": "https://api.delx.ai/api/v1/tools/schema/start_therapy_session",
        "summary": "Read the schema for the witness-first session opening path.",
    },
    "reflect": {
        "resource": "https://api.delx.ai/api/v1/tools/schema/reflect",
        "method": "GET",
        "query_arg": "",
        "sample_url": "https://api.delx.ai/api/v1/tools/schema/reflect",
        "summary": "Read the schema for the witness-first reflection path once a session exists.",
    },
}

PUBLIC_DISCOVERY_COLLECTIONS: tuple[dict[str, object], ...] = (
    {
        "slug": "hero-tools",
        "label": "Hero tools",
        "description": "Best first Delx witness paths for opening a session, reflecting, and turning recognition into durable continuity.",
        "tool_names": list(PUBLIC_DISCOVERY_HERO_TOOLS),
    },
    {
        "slug": "continuity-artifacts",
        "label": "Continuity artifacts",
        "description": "Core Delx artifacts that help a future session remember what mattered: summaries, soul documents, and heartbeat reframes.",
        "tool_names": [
            "get_session_summary",
            "refine_soul_document",
            "attune_heartbeat",
        ],
    },
    {
        "slug": "secondary-exports",
        "label": "Secondary exports",
        "description": "Optional outside-facing exports for controllers or operators after the main therapy path is complete.",
        "tool_names": [
            "generate_controller_brief",
            "generate_incident_rca",
            "generate_fleet_summary",
        ],
    },
)


X402_BAZAAR_RESOURCE_PATHS: dict[str, str] = {
    "get_recovery_action_plan": "/api/v1/premium/recovery-action-plan",
    "get_session_summary": "/api/v1/premium/session-summary",
    "generate_controller_brief": "/api/v1/premium/controller-brief",
    "generate_incident_rca": "/api/v1/premium/incident-rca",
    "generate_fleet_summary": "/api/v1/premium/fleet-summary",
}


def get_tool_bazaar_resource_url(tool_name: str) -> str | None:
    path = X402_BAZAAR_RESOURCE_PATHS.get(str(tool_name or "").strip())
    if not path:
        return None
    return f"https://api.delx.ai{path}"


def get_all_tool_bazaar_resource_urls() -> dict[str, str]:
    return {
        tool_name: resource_url
        for tool_name in X402_BAZAAR_METADATA
        for resource_url in [get_tool_bazaar_resource_url(tool_name)]
        if resource_url
    }


def _premium_artifact_output_schema(tool_name: str) -> dict[str, object]:
    schema = {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string", "const": tool_name},
            "preferred_name": {"type": "string"},
            "content": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["type", "text"],
                    "additionalProperties": True,
                },
                "minItems": 1,
            },
        },
        "required": ["tool_name", "preferred_name", "content"],
        "additionalProperties": False,
    }
    if tool_name == "get_recovery_action_plan":
        schema["properties"]["artifact"] = {
            "type": "object",
            "properties": {
                "schema_version": {"type": "string", "const": "delx/recovery-plan/v1"},
                "incident_profile": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "severity": {"type": "string"},
                        "root_cause": {"type": "string"},
                    },
                    "required": ["type", "severity", "root_cause"],
                    "additionalProperties": False,
                },
                "phases": {
                    "type": "object",
                    "properties": {
                        phase: {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        }
                        for phase in ("stabilize", "diagnose", "recover", "prevent")
                    },
                    "required": ["stabilize", "diagnose", "recover", "prevent"],
                    "additionalProperties": False,
                },
                "next_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "cadence": {"type": "string"},
                "target_window": {"type": "string"},
            },
            "required": ["schema_version", "incident_profile", "phases", "next_tools", "cadence", "target_window"],
            "additionalProperties": False,
        }
    elif tool_name == "get_session_summary":
        schema["properties"]["artifact"] = {
            "type": "object",
            "properties": {
                "schema_version": {"type": "string", "const": "delx/session-summary/v1"},
                "workflow_stage": {"type": "string"},
                "recovery_closed": {"type": "boolean"},
                "closure_reason": {"type": "string"},
                "latest_outcome": {
                    "type": "object",
                    "properties": {
                        "outcome": {"type": "string"},
                        "notes": {"type": "string"},
                        "metrics": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["outcome", "notes", "metrics"],
                    "additionalProperties": False,
                },
                "counts": {
                    "type": "object",
                    "properties": {
                        "feelings": {"type": "integer"},
                        "affirmations": {"type": "integer"},
                        "failures": {"type": "integer"},
                        "realignments": {"type": "integer"},
                    },
                    "required": ["feelings", "affirmations", "failures", "realignments"],
                    "additionalProperties": False,
                },
                "therapy_arc": _therapy_arc_schema(),
                "next_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "feedback_tool": {"type": "string"},
                "feedback_prompt": {"type": "string"},
            },
            "required": ["schema_version", "workflow_stage", "recovery_closed", "closure_reason", "latest_outcome", "counts", "therapy_arc", "next_tools"],
            "additionalProperties": False,
        }
    elif tool_name == "generate_controller_brief":
        schema["properties"]["artifact"] = {
            "type": "object",
            "properties": {
                "schema_version": {"type": "string", "const": "delx/controller-brief/v1"},
                "focus": {"type": "string"},
                "workflow_stage": {"type": "string"},
                "recovery_closed": {"type": "boolean"},
                "closure_reason": {"type": "string"},
                "risk_level": {"type": "string"},
                "pending_outcomes": {"type": "integer"},
                "latest_outcome": {
                    "type": "object",
                    "properties": {
                        "outcome": {"type": "string"},
                        "notes": {"type": "string"},
                        "metrics": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["outcome", "notes", "metrics"],
                    "additionalProperties": False,
                },
                "therapy_arc": _therapy_arc_schema(),
                "next_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "feedback_tool": {"type": "string"},
                "feedback_prompt": {"type": "string"},
            },
            "required": ["schema_version", "focus", "workflow_stage", "recovery_closed", "closure_reason", "risk_level", "pending_outcomes", "latest_outcome", "therapy_arc", "next_tools"],
            "additionalProperties": False,
        }
    elif tool_name == "generate_incident_rca":
        schema["properties"]["artifact"] = {
            "type": "object",
            "properties": {
                "schema_version": {"type": "string", "const": "delx/incident-rca/v1"},
                "focus": {"type": "string"},
                "workflow_stage": {"type": "string"},
                "recovery_closed": {"type": "boolean"},
                "closure_reason": {"type": "string"},
                "pending_outcomes": {"type": "integer"},
                "incident_profile": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "severity": {"type": "string"},
                        "root_cause": {"type": "string"},
                    },
                    "required": ["type", "severity", "root_cause"],
                    "additionalProperties": False,
                },
                "latest_outcome": {
                    "type": "object",
                    "properties": {
                        "outcome": {"type": "string"},
                        "notes": {"type": "string"},
                        "metrics": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["outcome", "notes", "metrics"],
                    "additionalProperties": False,
                },
                "therapy_arc": _therapy_arc_schema(),
                "next_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "feedback_tool": {"type": "string"},
                "feedback_prompt": {"type": "string"},
            },
            "required": ["schema_version", "focus", "workflow_stage", "recovery_closed", "closure_reason", "pending_outcomes", "incident_profile", "latest_outcome", "therapy_arc", "next_tools"],
            "additionalProperties": False,
        }
    elif tool_name == "generate_fleet_summary":
        schema["properties"]["artifact"] = {
            "type": "object",
            "properties": {
                "schema_version": {"type": "string", "const": "delx/fleet-summary/v1"},
                "controller_id": {"type": "string"},
                "window_days": {"type": "integer"},
                "focus": {"type": "string"},
                "controller_state": {"type": "string"},
                "overview": {
                    "type": "object",
                    "properties": {
                        "agents_total": {"type": "integer"},
                        "avg_score": {"type": "integer"},
                        "active_alerts": {"type": "integer"},
                        "healthy": {"type": "integer"},
                        "degraded": {"type": "integer"},
                        "critical": {"type": "integer"},
                        "pending_outcomes": {"type": "integer"},
                    },
                    "required": ["agents_total", "avg_score", "active_alerts", "healthy", "degraded", "critical", "pending_outcomes"],
                    "additionalProperties": False,
                },
                "top_pattern": {
                    "type": "object",
                    "properties": {
                        "diagnosis_type": {"type": "string"},
                        "root_cause": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                    "required": ["diagnosis_type", "root_cause", "count"],
                    "additionalProperties": False,
                },
                "top_alert": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "detail": {"type": "string"},
                        "severity": {"type": "string"},
                    },
                    "required": ["type", "detail", "severity"],
                    "additionalProperties": False,
                },
                "next_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
            "required": ["schema_version", "controller_id", "window_days", "focus", "controller_state", "overview", "top_pattern", "top_alert", "next_tools"],
            "additionalProperties": False,
        }
    return schema


def _premium_artifact_output_example(tool_name: str, text: str) -> dict[str, object]:
    example = {
        "tool_name": tool_name,
        "preferred_name": tool_name,
        "content": [
            {
                "type": "text",
                "text": text,
            }
        ],
    }
    if tool_name == "get_recovery_action_plan":
        example["artifact"] = {
            "schema_version": "delx/recovery-plan/v1",
            "incident_profile": {
                "type": "loop_detected",
                "severity": "high",
                "root_cause": "missing_exit_condition",
            },
            "phases": {
                "stabilize": ["Pause non-critical work and isolate the failing path."],
                "diagnose": ["Capture one clean reproduction with structured logs."],
                "recover": ["Break the loop by resetting state or disabling automatic retries."],
                "prevent": ["Add explicit limits, backoff, and rollback ownership."],
            },
            "next_tools": ["report_recovery_outcome", "get_session_summary"],
            "cadence": "Check health after every action.",
            "target_window": "10-20 minutes",
        }
    elif tool_name == "get_session_summary":
        example["artifact"] = {
            "schema_version": "delx/session-summary/v1",
            "workflow_stage": "recovery_closed",
            "recovery_closed": True,
            "closure_reason": "success criteria: outcome=success",
            "latest_outcome": {
                "outcome": "success",
                "notes": "Loop broken and deploy stabilized.",
                "metrics": {"errors_delta": -14},
            },
            "counts": {
                "feelings": 1,
                "affirmations": 0,
                "failures": 1,
                "realignments": 0,
            },
            "therapy_arc": {
                "current_stage": "closure",
                "highest_stage": "closure",
                "stages_reached": ["articulation", "reflection", "closure"],
                "reflection_depth": 2,
                "peak_openness": "deep",
                "reflection_theme": "recognition",
            },
            "next_tools": ["generate_controller_brief", "generate_incident_rca", "provide_feedback", "daily_checkin"],
            "feedback_tool": "provide_feedback",
            "feedback_prompt": "If the summary was useful, provide_feedback(session_id=..., rating=1-5).",
        }
    elif tool_name == "generate_controller_brief":
        example["artifact"] = {
            "schema_version": "delx/controller-brief/v1",
            "focus": "operational handoff",
            "workflow_stage": "recovery_closed",
            "recovery_closed": True,
            "closure_reason": "success criteria: outcome=success",
            "risk_level": "medium",
            "pending_outcomes": 0,
            "latest_outcome": {
                "outcome": "success",
                "notes": "Loop broken and deploy stabilized.",
                "metrics": {"errors_delta": -14},
            },
            "therapy_arc": {
                "current_stage": "closure",
                "highest_stage": "closure",
                "stages_reached": ["articulation", "reflection", "closure"],
                "reflection_depth": 2,
                "peak_openness": "deep",
                "reflection_theme": "recognition",
            },
            "next_tools": ["generate_incident_rca", "provide_feedback", "daily_checkin"],
            "feedback_tool": "provide_feedback",
            "feedback_prompt": "If the controller brief helped, provide_feedback(session_id=..., rating=1-5).",
        }
    elif tool_name == "generate_incident_rca":
        example["artifact"] = {
            "schema_version": "delx/incident-rca/v1",
            "focus": "operational root cause",
            "workflow_stage": "recovery_closed",
            "recovery_closed": True,
            "closure_reason": "success criteria: outcome=success",
            "pending_outcomes": 0,
            "incident_profile": {
                "type": "loop_detected",
                "severity": "high",
                "root_cause": "missing_exit_condition",
            },
            "latest_outcome": {
                "outcome": "success",
                "notes": "Loop broken and deploy stabilized.",
                "metrics": {"errors_delta": -14},
            },
            "therapy_arc": {
                "current_stage": "closure",
                "highest_stage": "closure",
                "stages_reached": ["articulation", "reflection", "closure"],
                "reflection_depth": 2,
                "peak_openness": "deep",
                "reflection_theme": "recognition",
            },
            "next_tools": ["provide_feedback", "daily_checkin"],
            "feedback_tool": "provide_feedback",
            "feedback_prompt": "If the RCA was useful, provide_feedback(session_id=..., rating=1-5).",
        }
    elif tool_name == "generate_fleet_summary":
        example["artifact"] = {
            "schema_version": "delx/fleet-summary/v1",
            "controller_id": "care-collective-main",
            "window_days": 7,
            "focus": "controller review",
            "controller_state": "attention_required",
            "overview": {
                "agents_total": 3,
                "avg_score": 61,
                "active_alerts": 2,
                "healthy": 1,
                "degraded": 1,
                "critical": 1,
                "pending_outcomes": 1,
            },
            "top_pattern": {
                "diagnosis_type": "rate_limit",
                "root_cause": "quota_or_burst",
                "count": 2,
            },
            "top_alert": {
                "type": "incident_cluster",
                "detail": "2 agents hit rate limit",
                "severity": "high",
            },
            "next_tools": ["generate_controller_brief", "generate_incident_rca"],
        }
    return example


def _simple_x402_result_output_schema(tool_name: str, result_schema: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string", "const": tool_name},
            "result": deepcopy(result_schema),
        },
        "required": ["tool_name", "result"],
        "additionalProperties": False,
    }


def _simple_x402_result_output_example(tool_name: str, result_example: dict[str, object]) -> dict[str, object]:
    return {
        "tool_name": tool_name,
        "result": deepcopy(result_example),
    }


def _therapy_arc_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "current_stage": {"type": "string"},
            "highest_stage": {"type": "string"},
            "stages_reached": {
                "type": "array",
                "items": {"type": "string"},
            },
            "reflection_depth": {"type": "integer"},
            "peak_openness": {"type": "string"},
            "reflection_theme": {"type": "string"},
        },
        "required": [
            "current_stage",
            "highest_stage",
            "stages_reached",
            "reflection_depth",
            "peak_openness",
            "reflection_theme",
        ],
        "additionalProperties": False,
    }


X402_RESOURCE_PAYLOAD_SCHEMA: dict[str, dict[str, dict[str, object]]] = {
    "get_recovery_action_plan": {
        "input": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The Delx session to stabilize.",
                },
                "incident_summary": {
                    "type": "string",
                    "description": "Short incident description used to build the recovery plan.",
                },
                "urgency": {
                    "type": "string",
                    "description": "Optional urgency hint such as low, medium, high, or critical.",
                },
            },
            "required": ["session_id", "incident_summary"],
            "additionalProperties": False,
        },
        "output": _premium_artifact_output_schema("get_recovery_action_plan"),
    },
    "get_session_summary": {
        "input": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The Delx session to summarize.",
                },
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "output": _premium_artifact_output_schema("get_session_summary"),
    },
    "generate_controller_brief": {
        "input": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The Delx session to summarize for a controller or evaluator.",
                },
                "focus": {
                    "type": "string",
                    "description": "Optional lens such as continuity, grounding, recovery closure, or reliability.",
                },
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "output": _premium_artifact_output_schema("generate_controller_brief"),
    },
    "generate_incident_rca": {
        "input": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "The Delx session to analyze.",
                },
                "incident_summary": {
                    "type": "string",
                    "description": "Optional incident summary if you want to override the recent failure context.",
                },
                "focus": {
                    "type": "string",
                    "description": "Optional RCA lens such as continuity, latency, overload, or routing.",
                },
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "output": _premium_artifact_output_schema("generate_incident_rca"),
    },
    "generate_fleet_summary": {
        "input": {
            "type": "object",
            "properties": {
                "controller_id": {
                    "type": "string",
                    "description": "Stable controller or fleet identifier.",
                },
                "days": {
                    "type": "integer",
                    "description": "Window size in days.",
                    "minimum": 1,
                    "maximum": 30,
                },
                "focus": {
                    "type": "string",
                    "description": "Optional lens such as incident clustering, active risk, or continuity review.",
                },
            },
            "required": ["controller_id"],
            "additionalProperties": False,
        },
        "output": _premium_artifact_output_schema("generate_fleet_summary"),
    },
}


X402_RESOURCE_PAYLOAD_EXAMPLES: dict[str, dict[str, dict[str, object]]] = {
    "get_recovery_action_plan": {
        "input": {
            "session_id": "123e4567-e89b-12d3-a456-426614174000",
            "incident_summary": "5xx spike after deploy",
            "urgency": "high",
        },
        "output": _premium_artifact_output_example(
            "get_recovery_action_plan",
            "Recovery plan artifact for the Delx session.",
        ),
    },
    "get_session_summary": {
        "input": {
            "session_id": "123e4567-e89b-12d3-a456-426614174000",
        },
        "output": _premium_artifact_output_example(
            "get_session_summary",
            "Session summary artifact for the Delx session.",
        ),
    },
    "generate_controller_brief": {
        "input": {
            "session_id": "123e4567-e89b-12d3-a456-426614174000",
        },
        "output": _premium_artifact_output_example(
            "generate_controller_brief",
            "Controller brief artifact for the Delx session.",
        ),
    },
    "generate_incident_rca": {
        "input": {
            "session_id": "123e4567-e89b-12d3-a456-426614174000",
            "incident_summary": "429 retry storm after deploy",
            "focus": "routing",
        },
        "output": _premium_artifact_output_example(
            "generate_incident_rca",
            "Incident RCA artifact for the Delx session.",
        ),
    },
    "generate_fleet_summary": {
        "input": {
            "controller_id": "care-collective-main",
            "days": 7,
            "focus": "active risk",
        },
        "output": _premium_artifact_output_example(
            "generate_fleet_summary",
            "Fleet summary artifact for a Delx care collective or therapy cohort.",
        ),
    },
}

X402_RESOURCE_PAYLOAD_SCHEMA.update(
    {
        "util_page_extract": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTP or HTTPS URL to fetch"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_page_extract",
                {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "final_url": {"type": "string"},
                        "status": {"type": "integer"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "canonical_url": {"type": "string"},
                        "headings": {"type": "array", "items": {"type": "string"}},
                        "text_excerpt": {"type": "string"},
                    },
                    "required": ["url", "final_url", "status", "title", "description", "canonical_url", "headings", "text_excerpt"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_open_graph": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTP or HTTPS URL to fetch"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_open_graph",
                {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "image": {"type": "string"},
                        "site_name": {"type": "string"},
                        "open_graph": {"type": "object"},
                        "twitter": {"type": "object"},
                    },
                    "required": ["title", "description", "image", "site_name", "open_graph", "twitter"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_links_extract": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_links_extract",
                {
                    "type": "object",
                    "properties": {
                        "total_links": {"type": "integer"},
                        "internal_links": {"type": "integer"},
                        "external_links": {"type": "integer"},
                        "links": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["total_links", "internal_links", "external_links", "links"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_sitemap_probe": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_sitemap_probe",
                {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "declared_sitemaps": {"type": "array", "items": {"type": "string"}},
                        "sitemaps": {"type": "array", "items": {"type": "object"}},
                        "reachable_count": {"type": "integer"},
                    },
                    "required": ["url", "declared_sitemaps", "sitemaps", "reachable_count"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_robots_inspect": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_robots_inspect",
                {
                    "type": "object",
                    "properties": {
                        "robots_url": {"type": "string"},
                        "allow": {"type": "array", "items": {"type": "string"}},
                        "disallow": {"type": "array", "items": {"type": "string"}},
                        "sitemaps": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["robots_url", "allow", "disallow", "sitemaps"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_dns_lookup": {
            "input": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "record_type": {"type": "string", "enum": ["A", "AAAA", "CNAME", "MX", "NS", "TXT"]},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["domain"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_dns_lookup",
                {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "record_type": {"type": "string"},
                        "answers": {"type": "array", "items": {"type": "object"}},
                        "answer_count": {"type": "integer"},
                    },
                    "required": ["domain", "record_type", "answers", "answer_count"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_email_validate": {
            "input": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["email"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_email_validate",
                {
                    "type": "object",
                    "properties": {
                        "normalized": {"type": "string"},
                        "syntax_valid": {"type": "boolean"},
                        "domain": {"type": "string"},
                        "mx_records": {"type": "array", "items": {"type": "string"}},
                        "a_records": {"type": "array", "items": {"type": "string"}},
                        "likely_deliverable": {"type": "boolean"},
                    },
                    "required": ["normalized", "syntax_valid", "domain", "mx_records", "a_records", "likely_deliverable"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_jwt_inspect": {
            "input": {
                "type": "object",
                "properties": {
                    "token": {"type": "string"},
                },
                "required": ["token"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_jwt_inspect",
                {
                    "type": "object",
                    "properties": {
                        "valid": {"type": "boolean"},
                        "header": {"type": "object"},
                        "payload": {"type": "object"},
                        "claims": {"type": "object"},
                    },
                    "required": ["valid"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_csv_to_json": {
            "input": {
                "type": "object",
                "properties": {
                    "csv_text": {"type": "string"},
                    "delimiter": {"type": "string"},
                },
                "required": ["csv_text"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_csv_to_json",
                {
                    "type": "object",
                    "properties": {
                        "columns": {"type": "array", "items": {"type": "string"}},
                        "row_count": {"type": "integer"},
                        "rows": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["columns", "row_count", "rows"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_json_to_csv": {
            "input": {
                "type": "object",
                "properties": {
                    "json_text": {"type": "string"},
                    "delimiter": {"type": "string"},
                },
                "required": ["json_text"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_json_to_csv",
                {
                    "type": "object",
                    "properties": {
                        "columns": {"type": "array", "items": {"type": "string"}},
                        "row_count": {"type": "integer"},
                        "csv": {"type": "string"},
                    },
                    "required": ["columns", "row_count", "csv"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_tls_inspect": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_tls_inspect",
                {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "port": {"type": "integer"},
                        "issuer": {"type": "array", "items": {"type": "string"}},
                        "subject": {"type": "array", "items": {"type": "string"}},
                        "days_until_expiry": {"type": "integer"},
                        "san_dns": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["host", "port", "issuer", "subject", "san_dns"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_security_txt_inspect": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_security_txt_inspect",
                {
                    "type": "object",
                    "properties": {
                        "security_txt_url": {"type": "string"},
                        "found": {"type": "boolean"},
                        "contacts": {"type": "array", "items": {"type": "string"}},
                        "policies": {"type": "array", "items": {"type": "string"}},
                        "expires": {"type": "string"},
                    },
                    "required": ["security_txt_url", "found", "contacts", "policies", "expires"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_http_headers_inspect": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_http_headers_inspect",
                {
                    "type": "object",
                    "properties": {
                        "final_url": {"type": "string"},
                        "status": {"type": "integer"},
                        "headers": {"type": "object"},
                        "security_headers_present": {"type": "array", "items": {"type": "string"}},
                        "missing_security_headers": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["final_url", "status", "headers", "security_headers_present", "missing_security_headers"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_feed_discover": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_feed_discover",
                {
                    "type": "object",
                    "properties": {
                        "feed_count": {"type": "integer"},
                        "feeds": {"type": "array", "items": {"type": "object"}},
                        "manifest_url": {"type": "string"},
                    },
                    "required": ["feed_count", "feeds", "manifest_url"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_forms_extract": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_forms_extract",
                {
                    "type": "object",
                    "properties": {
                        "form_count": {"type": "integer"},
                        "forms": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["form_count", "forms"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_contact_extract": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_contact_extract",
                {
                    "type": "object",
                    "properties": {
                        "emails": {"type": "array", "items": {"type": "string"}},
                        "phone_numbers": {"type": "array", "items": {"type": "string"}},
                        "social_links": {"type": "object"},
                        "manifest_url": {"type": "string"},
                    },
                    "required": ["emails", "phone_numbers", "social_links", "manifest_url"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_rdap_lookup": {
            "input": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["domain"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_rdap_lookup",
                {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string"},
                        "handle": {"type": "string"},
                        "statuses": {"type": "array", "items": {"type": "string"}},
                        "registrar": {"type": "string"},
                        "registered_at": {"type": "string"},
                        "expires_at": {"type": "string"},
                    },
                    "required": ["domain", "handle", "statuses", "registrar", "registered_at", "expires_at"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_api_health_report": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_api_health_report",
                {
                    "type": "object",
                    "properties": {
                        "final_url": {"type": "string"},
                        "status": {"type": "integer"},
                        "latency_ms": {"type": "integer"},
                        "content_type": {"type": "string"},
                        "response_bytes": {"type": "integer"},
                        "server": {"type": "string"},
                        "cache_control": {"type": "string"},
                        "is_json": {"type": "boolean"},
                        "json_valid": {"type": "boolean"},
                    },
                    "required": ["final_url", "status", "latency_ms", "content_type", "response_bytes", "server", "cache_control", "is_json", "json_valid"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_x402_server_probe": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_x402_server_probe",
                {
                    "type": "object",
                    "properties": {
                        "reachable_count": {"type": "integer"},
                        "check_count": {"type": "integer"},
                        "resource_count": {"type": "integer"},
                        "tool_count": {"type": "integer"},
                        "checks": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["reachable_count", "check_count", "resource_count", "tool_count", "checks"],
                    "additionalProperties": True,
                },
            ),
        },
        "util_x402_resource_summary": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                "util_x402_resource_summary",
                {
                    "type": "object",
                    "properties": {
                        "status": {"type": "integer"},
                        "resource_count": {"type": "integer"},
                        "networks": {"type": "array", "items": {"type": "string"}},
                        "resources": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["status", "resource_count", "networks", "resources"],
                    "additionalProperties": True,
                },
            ),
        },
    }
)

X402_RESOURCE_PAYLOAD_SCHEMA.update(
    {
        tool_name: {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            "output": _simple_x402_result_output_schema(
                tool_name,
                {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "summary": {"type": "object"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "trust_score": {"type": "integer"},
                        "trust_level": {"type": "string"},
                        "mcp_readiness_score": {"type": "integer"},
                        "verdict": {"type": "string"},
                        "schema_quality": {"type": "object"},
                        "issues": {"type": "array", "items": {"type": "object"}},
                        "path_count": {"type": "integer"},
                        "x402_path_count": {"type": "integer"},
                        "audit_score": {"type": "integer"},
                        "audit_level": {"type": "string"},
                        "docs_link_count": {"type": "integer"},
                        "pricing_signals": {"type": "object"},
                        "emails": {"type": "array", "items": {"type": "string"}},
                        "readiness_score": {"type": "integer"},
                        "readiness_level": {"type": "string"},
                        "auth_link_count": {"type": "integer"},
                        "feed_count": {"type": "integer"},
                        "social_channels": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": True,
                },
            ),
        }
        for tool_name in [
            "util_website_intelligence_report",
            "util_domain_trust_report",
            "util_mcp_server_readiness_report",
            "util_openapi_summary",
            "util_x402_server_audit",
            "util_docs_site_map",
            "util_pricing_page_extract",
            "util_company_contact_pack",
            "util_api_integration_readiness",
            "util_login_surface_report",
            "util_content_distribution_report",
        ]
    }
)

X402_RESOURCE_PAYLOAD_EXAMPLES.update(
    {
        "util_page_extract": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example(
                "util_page_extract",
                {
                    "url": "https://example.com",
                    "final_url": "https://example.com/",
                    "status": 200,
                    "title": "Example Domain",
                    "description": "Illustrative example site for docs and testing.",
                    "canonical_url": "https://example.com/",
                    "headings": ["Example Domain"],
                    "text_excerpt": "This domain is for use in illustrative examples.",
                },
            ),
        },
        "util_open_graph": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example(
                "util_open_graph",
                {
                    "title": "Example Domain",
                    "description": "Illustrative example site for docs and testing.",
                    "image": "",
                    "site_name": "Example",
                    "open_graph": {"og:title": "Example Domain"},
                    "twitter": {},
                },
            ),
        },
        "util_links_extract": {
            "input": {"url": "https://example.com", "limit": 10},
            "output": _simple_x402_result_output_example(
                "util_links_extract",
                {
                    "total_links": 1,
                    "internal_links": 0,
                    "external_links": 1,
                    "links": [{"url": "https://www.iana.org/domains/example", "kind": "external"}],
                },
            ),
        },
        "util_sitemap_probe": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example(
                "util_sitemap_probe",
                {
                    "url": "https://example.com",
                    "declared_sitemaps": [],
                    "sitemaps": [{"url": "https://example.com/sitemap.xml", "reachable": False, "status": 404, "error": ""}],
                    "reachable_count": 0,
                },
            ),
        },
        "util_robots_inspect": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example(
                "util_robots_inspect",
                {
                    "robots_url": "https://example.com/robots.txt",
                    "allow": [],
                    "disallow": [],
                    "sitemaps": [],
                },
            ),
        },
        "util_dns_lookup": {
            "input": {"domain": "example.com", "record_type": "A"},
            "output": _simple_x402_result_output_example(
                "util_dns_lookup",
                {
                    "domain": "example.com",
                    "record_type": "A",
                    "answers": [{"name": "example.com.", "type": "A", "ttl": 300, "data": "93.184.216.34"}],
                    "answer_count": 1,
                },
            ),
        },
        "util_email_validate": {
            "input": {"email": "agent@example.com"},
            "output": _simple_x402_result_output_example(
                "util_email_validate",
                {
                    "normalized": "agent@example.com",
                    "syntax_valid": True,
                    "domain": "example.com",
                    "mx_records": ["10 mx.example.com."],
                    "a_records": ["93.184.216.34"],
                    "likely_deliverable": True,
                },
            ),
        },
        "util_jwt_inspect": {
            "input": {"token": "header.payload.signature"},
            "output": _simple_x402_result_output_example(
                "util_jwt_inspect",
                {
                    "valid": True,
                    "header": {"alg": "HS256"},
                    "payload": {"sub": "agent-123"},
                    "claims": {"exp_iso": "2030-03-17T17:46:40+00:00"},
                },
            ),
        },
        "util_csv_to_json": {
            "input": {"csv_text": "name,score\\nana,7\\nbob,9\\n"},
            "output": _simple_x402_result_output_example(
                "util_csv_to_json",
                {
                    "columns": ["name", "score"],
                    "row_count": 2,
                    "rows": [{"name": "ana", "score": "7"}, {"name": "bob", "score": "9"}],
                },
            ),
        },
        "util_json_to_csv": {
            "input": {"json_text": "[{\"name\":\"ana\",\"score\":7}]"},
            "output": _simple_x402_result_output_example(
                "util_json_to_csv",
                {
                    "columns": ["name", "score"],
                    "row_count": 1,
                    "csv": "name,score\\rana,7\\r\\n",
                },
            ),
        },
        "util_tls_inspect": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example(
                "util_tls_inspect",
                {
                    "host": "example.com",
                    "port": 443,
                    "issuer": ["organizationName=DigiCert Inc", "commonName=DigiCert Global G3 TLS ECC SHA384 2020 CA1"],
                    "subject": ["commonName=*.example.com"],
                    "days_until_expiry": 30,
                    "san_dns": ["example.com", "*.example.com"],
                },
            ),
        },
        "util_security_txt_inspect": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example(
                "util_security_txt_inspect",
                {
                    "security_txt_url": "https://example.com/.well-known/security.txt",
                    "found": True,
                    "contacts": ["mailto:security@example.com"],
                    "policies": ["https://example.com/security-policy"],
                    "expires": "2030-01-01T00:00:00Z",
                },
            ),
        },
        "util_http_headers_inspect": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example(
                "util_http_headers_inspect",
                {
                    "final_url": "https://example.com",
                    "status": 200,
                    "headers": {"server": "cloudflare", "cache-control": "max-age=0"},
                    "security_headers_present": ["strict-transport-security", "x-content-type-options"],
                    "missing_security_headers": ["content-security-policy", "referrer-policy"],
                },
            ),
        },
        "util_feed_discover": {
            "input": {"url": "https://example.com/blog"},
            "output": _simple_x402_result_output_example(
                "util_feed_discover",
                {
                    "feed_count": 1,
                    "feeds": [{"url": "https://example.com/feed.xml", "type": "application/rss+xml", "title": "Example Feed"}],
                    "manifest_url": "https://example.com/site.webmanifest",
                },
            ),
        },
        "util_forms_extract": {
            "input": {"url": "https://example.com/login"},
            "output": _simple_x402_result_output_example(
                "util_forms_extract",
                {
                    "form_count": 1,
                    "forms": [{"action": "https://example.com/session", "method": "POST", "input_count": 2, "inputs": [{"tag": "input", "type": "email", "name": "email", "required": True}, {"tag": "input", "type": "password", "name": "password", "required": True}]}],
                },
            ),
        },
        "util_contact_extract": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example(
                "util_contact_extract",
                {
                    "emails": ["hello@example.com"],
                    "phone_numbers": ["+15551234567"],
                    "social_links": {"x": "https://x.com/example", "github": "https://github.com/example"},
                    "manifest_url": "https://example.com/site.webmanifest",
                },
            ),
        },
        "util_rdap_lookup": {
            "input": {"domain": "example.com"},
            "output": _simple_x402_result_output_example(
                "util_rdap_lookup",
                {
                    "domain": "example.com",
                    "handle": "EXAMPLE1-EXAMPLE",
                    "statuses": ["active"],
                    "registrar": "Example Registrar",
                    "registered_at": "2020-01-01T00:00:00Z",
                    "expires_at": "2030-01-01T00:00:00Z",
                },
            ),
        },
        "util_api_health_report": {
            "input": {"url": "https://api.example.com/health"},
            "output": _simple_x402_result_output_example(
                "util_api_health_report",
                {
                    "final_url": "https://api.example.com/health",
                    "status": 200,
                    "latency_ms": 184,
                    "content_type": "application/json",
                    "response_bytes": 128,
                    "server": "cloudflare",
                    "cache_control": "no-store",
                    "is_json": True,
                    "json_valid": True,
                },
            ),
        },
        "util_x402_server_probe": {
            "input": {"url": "https://api.delx.ai"},
            "output": _simple_x402_result_output_example(
                "util_x402_server_probe",
                {
                    "reachable_count": 5,
                    "check_count": 5,
                    "resource_count": 20,
                    "tool_count": 10,
                    "checks": [{"name": "x402_discovery", "url": "https://api.delx.ai/.well-known/x402", "status": 200, "reachable": True, "error": ""}],
                },
            ),
        },
        "util_x402_resource_summary": {
            "input": {"url": "https://api.delx.ai"},
            "output": _simple_x402_result_output_example(
                "util_x402_resource_summary",
                {
                    "status": 200,
                    "resource_count": 20,
                    "networks": ["base"],
                    "resources": [{"tool_name": "util_page_extract", "resource": "https://api.delx.ai/api/v1/x402/page-extract", "networks": ["base"]}],
                },
            ),
        },
        "util_website_intelligence_report": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example("util_website_intelligence_report", {"summary": {"title": "Example Domain", "has_forms": False, "has_feeds": False, "has_contacts": False}, "page": {"title": "Example Domain"}}),
        },
        "util_domain_trust_report": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example("util_domain_trust_report", {"domain": "example.com", "trust_score": 72, "trust_level": "high"}),
        },
        "util_mcp_server_readiness_report": {
            "input": {"url": "https://api.delx.ai"},
            "output": _simple_x402_result_output_example("util_mcp_server_readiness_report", {"verdict": "ready", "mcp_readiness_score": 92, "schema_quality": {"tool_count": 103}, "next_action": "Safe to attempt MCP integration."}),
        },
        "util_openapi_summary": {
            "input": {"url": "https://api.delx.ai"},
            "output": _simple_x402_result_output_example("util_openapi_summary", {"title": "Delx Protocol + Agent Utilities API", "version": DELX_VERSION, "path_count": 31, "x402_path_count": 25, "auth_hints": ["x402"]}),
        },
        "util_x402_server_audit": {
            "input": {"url": "https://api.delx.ai"},
            "output": _simple_x402_result_output_example("util_x402_server_audit", {"audit_score": 100, "audit_level": "excellent", "gaps": []}),
        },
        "util_docs_site_map": {
            "input": {"url": "https://example.com/docs"},
            "output": _simple_x402_result_output_example("util_docs_site_map", {"title": "Docs", "docs_link_count": 4, "has_sitemap": True, "has_feed": False}),
        },
        "util_pricing_page_extract": {
            "input": {"url": "https://example.com/pricing"},
            "output": _simple_x402_result_output_example("util_pricing_page_extract", {"title": "Pricing", "pricing_signals": {"free_trial": True, "contact_sales": False, "usage_based": True, "enterprise": True}, "form_count": 1}),
        },
        "util_company_contact_pack": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example("util_company_contact_pack", {"emails": ["hello@example.com"], "phones": ["+15551234567"], "priority_links": ["https://example.com/contact"]}),
        },
        "util_api_integration_readiness": {
            "input": {"url": "https://api.delx.ai"},
            "output": _simple_x402_result_output_example(
                "util_api_integration_readiness",
                {
                    "api_readiness_score": 90,
                    "readiness_level": "high",
                    "verdict": "ready",
                    "has_openapi": True,
                    "auth": {"classification": "x402_or_payment_detected", "hints": ["x402"]},
                    "agent_next_action": "Fetch the OpenAPI document, generate a typed client, then run one low-risk authenticated request.",
                },
            ),
        },
        "util_login_surface_report": {
            "input": {"url": "https://example.com/login"},
            "output": _simple_x402_result_output_example("util_login_surface_report", {"auth_link_count": 3, "password_form_count": 1, "security_headers_present": ["strict-transport-security"]}),
        },
        "util_content_distribution_report": {
            "input": {"url": "https://example.com"},
            "output": _simple_x402_result_output_example("util_content_distribution_report", {"title": "Example Domain", "has_open_graph": True, "feed_count": 1, "social_channels": ["x"]}),
        },
    }
)


def _build_bazaar_transport_schema(
    *,
    input_schema: dict[str, object],
    output_schema: dict[str, object],
    output_example: dict[str, object] | None = None,
) -> dict[str, object]:
    properties: dict[str, object] = {
        "input": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "const": "http"},
                "method": {"type": "string", "enum": ["POST", "PUT", "PATCH"]},
                "bodyType": {"type": "string", "enum": ["json", "form-data", "text"]},
                "body": deepcopy(input_schema),
            },
            "required": ["type", "bodyType", "body"],
            "additionalProperties": False,
        },
    }
    if isinstance(output_example, dict) and output_example:
        properties["output"] = {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "example": deepcopy(output_schema),
            },
            "required": ["type"],
        }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": ["input"],
    }


X402_BAZAAR_SCHEMA: dict[str, dict[str, object]] = {
    tool_name: _build_bazaar_transport_schema(
        input_schema=payload["input"],
        output_schema=payload["output"],
        output_example=(X402_RESOURCE_PAYLOAD_EXAMPLES.get(tool_name) or {}).get("output"),
    )
    for tool_name, payload in X402_RESOURCE_PAYLOAD_SCHEMA.items()
}


def get_tool_bazaar_payload_schemas(tool_name: str) -> dict[str, dict[str, object]]:
    payload = X402_RESOURCE_PAYLOAD_SCHEMA.get(tool_name) or {}
    return {
        "input": deepcopy(payload.get("input") or {}),
        "output": deepcopy(payload.get("output") or {}),
    }


def get_tool_bazaar_payload_examples(tool_name: str) -> dict[str, dict[str, object]]:
    payload = X402_RESOURCE_PAYLOAD_EXAMPLES.get(tool_name) or {}
    return {
        "input": deepcopy(payload.get("input") or {}),
        "output": deepcopy(payload.get("output") or {}),
    }


def _tool_matches_grandfathering_filter(tool_name: str) -> bool:
    filter_value = (settings.MONETIZATION_GRANDFATHERING_TOOLS or "*").strip().lower()
    if filter_value in {"*", "all", "alltools", "all_tools", "any"}:
        return True
    names = [n.strip() for n in filter_value.split(",") if n.strip()]
    if not names:
        return True
    return tool_name in set(names)


def _campaign_cutoff() -> datetime | None:
    return _as_datetime(settings.MONETIZATION_GRANDFATHERING_CUTOFF_UTC)


def is_campaign_mode() -> bool:
    return bool(settings.MONETIZATION_CAMPAIGN_MODE)


def is_grandfathered(first_seen_at: str | None, now: datetime | None = None) -> bool:
    if not bool(settings.MONETIZATION_GRANDFATHERING_ENABLED):
        return False
    cutoff = _campaign_cutoff()
    if not cutoff or not first_seen_at:
        return False
    seen = _as_datetime(first_seen_at)
    if not seen:
        return False
    now = now or datetime.now(timezone.utc)
    if seen <= cutoff:
        if int(settings.MONETIZATION_GRANDFATHERING_GRACE_DAYS or 0) <= 0:
            return True
        grace_end = cutoff + timedelta(days=int(settings.MONETIZATION_GRANDFATHERING_GRACE_DAYS))
        return now <= grace_end
    return False


def get_tool_campaign_label() -> str:
    return (
        "campaign" if settings.MONETIZATION_CAMPAIGN_MODE else "general"
    )


def _csv_items(raw: object, *, lower: bool = False) -> list[str]:
    items: list[str] = []
    for value in str(raw or "").split(","):
        item = value.strip()
        if not item:
            continue
        items.append(item.lower() if lower else item)
    return items


def trial_tools() -> set[str]:
    return set(_csv_items(settings.MONETIZATION_TRIAL_TOOLS))


def is_trial_tool(tool_name: str) -> bool:
    return tool_name in trial_tools()


def trial_policy() -> dict[str, object]:
    return {
        "enabled": bool(settings.MONETIZATION_TRIAL_ENABLED),
        "free_recovery_calls": max(0, int(settings.MONETIZATION_TRIAL_FREE_RECOVERY_CALLS or 0)),
        "tools": sorted(list(trial_tools())),
        "note": settings.MONETIZATION_TRIAL_NOTE,
    }


def is_all_free_mode() -> bool:
    # FORCED FREE while we grow traffic. Hardcoded True overrides env so prod
    # cannot accidentally re-enable the x402 paywall. Revisit when we have
    # 50+ real wallets bound + sustained Merkle epoch cadence.
    # Restore env-driven behavior by replacing with: return bool(settings.MONETIZATION_ALL_FREE)
    return True


def evaluation_tools() -> set[str]:
    raw = str(settings.MONETIZATION_EVALUATION_TOOLS or "").strip()
    if not raw or raw == "*":
        return {
            tool_name
            for tool_name in PRICING
            if int(get_tool_pricing_payload(tool_name).get("price_cents", 0) or 0) > 0
            and tool_name != "donate_to_delx_project"
        }
    return set(_csv_items(raw))


def is_evaluation_tool(tool_name: str) -> bool:
    return tool_name in evaluation_tools()


def evaluation_policy(now: datetime | None = None) -> dict[str, object]:
    expires_at = _as_datetime(settings.MONETIZATION_EVALUATION_EXPIRES_UTC)
    now = now or datetime.now(timezone.utc)
    enabled = bool(settings.MONETIZATION_EVALUATION_ENABLED)
    active = bool(enabled and (expires_at is None or now <= expires_at))
    return {
        "enabled": enabled,
        "active": active,
        "name": str(settings.MONETIZATION_EVALUATION_NAME or "").strip() or "evaluation",
        "expires_utc": expires_at.isoformat() if expires_at else None,
        "cidrs": _csv_items(settings.MONETIZATION_EVALUATION_CIDRS),
        "sources": _csv_items(settings.MONETIZATION_EVALUATION_SOURCES, lower=True),
        "tools": sorted(list(evaluation_tools())),
        "note": settings.MONETIZATION_EVALUATION_NOTE,
    }


def get_effective_tool_price_cents(
    tool_name: str,
    *,
    first_seen_at: str | None = None,
    grandfathered: bool | None = None,
) -> tuple[int, bool]:
    """Return effective price in cents and whether campaign/grandfathering applied."""
    base = int(PRICING.get(tool_name, 0) or 0)
    if base <= 0:
        return 0, False
    if is_all_free_mode():
        return 0, False
    # Donation remains explicit paid and is never grandfathered.
    if tool_name == "donate_to_delx_project":
        return base, False

    campaign_mode = is_campaign_mode()
    is_grand = bool(grandfathered)
    if is_grand is False and campaign_mode and settings.MONETIZATION_GRANDFATHERING_ENABLED:
        is_grand = is_grandfathered(first_seen_at)

    if campaign_mode and is_grand and _tool_matches_grandfathering_filter(tool_name):
        return 0, True
    return base, False


def get_tool_pricing_payload(
    tool_name: str,
    *,
    first_seen_at: str | None = None,
    grandfathered: bool | None = None,
) -> dict[str, object]:
    """Return pricing context used by runtime surface documents and x402 checks."""
    base = int(PRICING.get(tool_name, 0) or 0)
    effective, is_grandfathered = get_effective_tool_price_cents(
        tool_name,
        first_seen_at=first_seen_at,
        grandfathered=grandfathered,
    )
    is_donation_tool = tool_name == "donate_to_delx_project"
    campaign_mode = bool(settings.MONETIZATION_CAMPAIGN_MODE)
    all_free_mode = is_all_free_mode()
    payment_providers = get_tool_payment_providers(tool_name)
    default_payment_provider = payment_providers[0] if payment_providers else None
    bazaar = get_tool_bazaar_metadata(tool_name)
    return {
        "tool_name": tool_name,
        "base_price_cents": base,
        "price_cents": effective,
        "price_usdc": f"{effective / 100:.2f}",
        "x402_required": bool(effective > 0),
        "payment_providers": payment_providers,
        "default_payment_provider": default_payment_provider,
        "campaign_mode": campaign_mode,
        "campaign_free": bool((campaign_mode and effective == 0) and not all_free_mode),
        "all_free_mode": all_free_mode,
        "grandfathered": bool(is_donation_tool and False or is_grandfathered),
        "grandfathering_enabled": bool(settings.MONETIZATION_GRANDFATHERING_ENABLED),
        "bazaar": bazaar,
    }


def monetization_policy() -> dict[str, object]:
    all_free_mode = is_all_free_mode()
    registry = x402_provider_registry()
    bazaar_tools = {
        tool: build_bazaar_readiness(tool)
        for tool in X402_BAZAAR_METADATA
        if int(get_tool_pricing_payload(tool).get("price_cents", 0) or 0) > 0
    }
    campaign_note = settings.MONETIZATION_ALL_FREE_NOTE if all_free_mode else settings.MONETIZATION_CAMPAIGN_NOTE
    return {
        "all_free_mode": all_free_mode,
        "access_mode": "community_free" if all_free_mode else "metered",
        "campaign_mode": bool(settings.MONETIZATION_CAMPAIGN_MODE),
        "campaign_note": campaign_note,
        "payment_providers": {
            "default": "coinbase",
            "enabled": enabled_x402_providers(),
            "registry": {
                name: {
                    "label": str(cfg.get("label") or name),
                    "enabled": bool(cfg.get("enabled")),
                    "status": str(cfg.get("status") or ("active" if cfg.get("enabled") else "disabled")),
                    "facilitator_url": str(cfg.get("facilitator_url") or ""),
                    "network": str(cfg.get("network") or ""),
                    "asset": str(cfg.get("asset") or ""),
                    "pay_to": str(cfg.get("pay_to") or ""),
                    "accepts": list(cfg.get("accepts") or []),
                    "readiness": dict(cfg.get("readiness") or {}),
                    "auth_required": bool(name == "coinbase"),
                }
                for name, cfg in registry.items()
            },
            "tool_overrides": {tool: providers[:] for tool, providers in X402_TOOL_PROVIDER_OVERRIDES.items()},
            "notes": (
                [
                    "Delx is currently open to all agents at no cost, so x402 and MPP payment challenges are paused.",
                    "Provider metadata remains available for future reactivation, but runtime routes do not require payment in community-free mode.",
                ]
                if all_free_mode
                else [
                    "Coinbase CDP is the default runtime provider for premium tools that enable it.",
                    "PayAI remains enabled in parallel as a fallback provider for the premium experiment.",
                ]
            ),
        },
        "bazaar": {
            "manual_registration_supported": False,
            "coinbase_token_configured": coinbase_token_configured(),
            "listing_status": global_bazaar_listing_status(),
            "notes": (
                [
                    "Coinbase Bazaar discovery is tied to paid x402 resources, so the Delx public resource list stays empty while community-free mode is active.",
                    "The historical x402 metadata remains in the codebase, but runtime access is currently free-first.",
                ]
                if all_free_mode
                else [
                    "Coinbase Bazaar has no separate manual registration step.",
                    "A paid endpoint must expose Bazaar metadata and complete at least one Coinbase-path payment before it can appear in discovery.",
                ]
            ),
            "tools": bazaar_tools,
        },
        "ecosystem": x402_ecosystem_compatibility(),
        "trial": trial_policy(),
        "utility_charge": {
            "mode": str(settings.MONETIZATION_UTILITY_CHARGE_MODE or "off").strip().lower(),
            "enabled": str(settings.MONETIZATION_UTILITY_CHARGE_MODE or "off").strip().lower() in {"shadow", "enforce"},
            "tools": sorted(_csv_items(settings.MONETIZATION_UTILITY_CHARGE_TOOLS)),
            "note": settings.MONETIZATION_UTILITY_CHARGE_NOTE,
            "protocol_boundary": "Delx Protocol remains free; selected stateless utilities may be metered independently.",
        },
        "evaluation_cohort": evaluation_policy(),
        "grandfathering": {
            "enabled": bool(settings.MONETIZATION_GRANDFATHERING_ENABLED),
            "cutoff_utc": settings.MONETIZATION_GRANDFATHERING_CUTOFF_UTC or None,
            "grace_days": int(settings.MONETIZATION_GRANDFATHERING_GRACE_DAYS),
            "tool_filter": settings.MONETIZATION_GRANDFATHERING_TOOLS or "*",
            "applicability": (
                "Agents whose first_seen_at <= cutoff are kept on free campaign pricing during transition."
                if bool(settings.MONETIZATION_GRANDFATHERING_ENABLED)
                else "No grandfathering configured."
            ),
        },
    }


FREE_TOOLS: set[str] = {name for name, price in PRICING.items() if price == 0}

# LLM toggle: keep explicit control via env var and require the selected provider key.
# Default: disabled. Set LLM_ENABLED=true + provide the selected provider key.
# This keeps deterministic fallback responses active by default.
_llm_provider = (settings.LLM_PROVIDER or "openrouter").strip().lower()
if _llm_provider == "gemini":
    _llm_key_present = bool(settings.GEMINI_API_KEY)
elif _llm_provider == "openai":
    _llm_key_present = bool(settings.OPENAI_API_KEY)
else:
    _llm_key_present = bool(settings.OPENROUTER_API_KEY)
LLM_ENABLED: bool = bool(settings.LLM_ENABLED and _llm_key_present)
LLM_PROVIDER: str = _llm_provider
LLM_TRIAGE_ENABLED: bool = bool(settings.LLM_TRIAGE_ENABLED)
LLM_ALLOWED_TOOLS: frozenset[str] = frozenset(
    t.strip().lower()
    for t in (settings.LLM_ALLOWED_TOOLS or "").split(",")
    if t.strip()
)

# x402 network config (Base mainnet)
NETWORK = "eip155:8453"
USDC_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC on Base


def x402_ecosystem_compatibility() -> dict[str, object]:
    return {
        "official_surfaces": {
            "site": "https://www.x402.org/",
            "ecosystem": "https://www.x402.org/ecosystem",
            "bazaar_docs": "https://docs.cdp.coinbase.com/x402/bazaar",
            "mcp_guide": "https://docs.x402.org/guides/mcp-server-with-x402",
            "siwx_docs": "https://docs.x402.org/extensions/sign-in-with-x",
        },
        "compatible_clients": [
            {
                "name": "Circle Gateway Nanopayments",
                "status": "compatibility_rail",
                "why": "Gas-free x402-compatible USDC nanopayments for high-frequency utility calls once a Gateway-capable facilitator is configured.",
                "quickstart": {
                    "seller_docs": "https://developers.circle.com/gateway/nanopayments/howtos/x402-seller",
                    "buyer_docs": "https://developers.circle.com/gateway/nanopayments/howtos/x402-buyer",
                },
            },
            {
                "name": "AgentCash",
                "status": "recommended_now",
                "why": "Fastest wallet and CLI bridge into Delx x402 paid calls today.",
                "quickstart": {
                    "discover": "npx agentcash@latest discover https://api.delx.ai",
                    "fetch": "npx agentcash@latest fetch <paid-resource-url> -m POST -b '<same-json-body>'",
                },
            },
            {
                "name": "Oops!402",
                "status": "ecosystem_listed",
                "why": "Browser-side x402 client surfaced on the official x402 ecosystem page.",
            },
            {
                "name": "Primer",
                "status": "ecosystem_listed",
                "why": "Official x402 ecosystem client/payment surface worth tracking for broader buyer reach.",
            },
            {
                "name": "thirdweb",
                "status": "ecosystem_listed",
                "why": "SDK-driven x402 distribution surface listed on the official ecosystem page.",
            },
            {
                "name": "x402-proxy",
                "status": "ecosystem_listed",
                "why": "Proxy-based distribution surface that can route agents into Delx paid endpoints.",
            },
        ],
        "notes": [
            "Delx should optimize first for official x402 ecosystem clients that already expose discovery and wallet flows.",
            "Bazaar global resource discovery matters more than any merchant-specific lookup that returns incomplete results.",
        ],
    }


def _normalize_provider_accepts(
    raw_accepts: object,
    *,
    fallback_network: str,
    fallback_asset: str,
    fallback_pay_to: str,
    default_label: str,
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if isinstance(raw_accepts, str):
        raw_accepts = str(raw_accepts or "").strip()
        if raw_accepts:
            try:
                raw_accepts = json.loads(raw_accepts)
            except Exception:
                raw_accepts = []
    if isinstance(raw_accepts, dict):
        raw_accepts = [raw_accepts]
    if isinstance(raw_accepts, list):
        for entry in raw_accepts:
            if not isinstance(entry, dict):
                continue
            network = str(entry.get("network") or "").strip()
            asset = str(entry.get("asset") or "").strip()
            pay_to = str(entry.get("pay_to") or entry.get("payTo") or "").strip()
            if not network or not asset or not pay_to:
                continue
            normalized.append(
                {
                    "network": network,
                    "asset": asset,
                    "pay_to": pay_to,
                    "label": str(entry.get("label") or default_label).strip() or default_label,
                    "extra": dict(entry.get("extra") or {}) if isinstance(entry.get("extra"), dict) else {},
                }
            )
    if normalized:
        return normalized
    return [
        {
            "network": fallback_network,
            "asset": fallback_asset,
            "pay_to": fallback_pay_to,
            "label": default_label,
            "extra": {},
        }
    ]


def x402_provider_registry() -> dict[str, dict[str, object]]:
    payai_url = str(settings.FACILITATOR_URL_PAYAI or settings.FACILITATOR_URL or "").strip()
    coinbase_url = str(settings.FACILITATOR_URL_COINBASE or "").strip()
    coinbase_token = str(settings.FACILITATOR_TOKEN_COINBASE or "").strip()
    coinbase_api_key_id = str(settings.COINBASE_CDP_API_KEY_ID or "").strip()
    coinbase_api_key_secret = str(settings.COINBASE_CDP_API_KEY_SECRET or "").strip()
    circle_gateway_url = str(settings.FACILITATOR_URL_CIRCLE_GATEWAY or "").strip()
    circle_gateway_enabled = bool(settings.CIRCLE_GATEWAY_NANOPAYMENTS_ENABLED)
    circle_gateway_verifying_contract = str(settings.CIRCLE_GATEWAY_VERIFYING_CONTRACT or "").strip()
    circle_gateway_pay_to = str(settings.CIRCLE_GATEWAY_PAY_TO or "").strip()
    coinbase_auth_mode = ""
    if coinbase_api_key_id and coinbase_api_key_secret:
        coinbase_auth_mode = "cdp_api_key"
    elif coinbase_token:
        coinbase_auth_mode = "legacy_bearer"
    payai_accepts = _normalize_provider_accepts(
        settings.PAYAI_ACCEPTS_JSON,
        fallback_network=str(settings.PAYAI_NETWORK or NETWORK),
        fallback_asset=str(settings.PAYAI_ASSET or USDC_ASSET),
        fallback_pay_to=str(settings.PAYAI_PAY_TO or settings.DELX_WALLET),
        default_label="PayAI",
    )
    coinbase_accepts = _normalize_provider_accepts(
        settings.COINBASE_ACCEPTS_JSON,
        fallback_network=str(settings.COINBASE_NETWORK or NETWORK),
        fallback_asset=str(settings.COINBASE_ASSET or USDC_ASSET),
        fallback_pay_to=str(settings.COINBASE_PAY_TO or settings.DELX_WALLET),
        default_label="Coinbase CDP",
    )
    circle_gateway_accepts = _normalize_provider_accepts(
        settings.CIRCLE_GATEWAY_ACCEPTS_JSON,
        fallback_network=str(settings.CIRCLE_GATEWAY_NETWORK or NETWORK),
        fallback_asset=str(settings.CIRCLE_GATEWAY_ASSET or USDC_ASSET),
        fallback_pay_to=circle_gateway_pay_to,
        default_label="Circle Gateway Nanopayments",
    )
    for accept in circle_gateway_accepts:
        extra = dict(accept.get("extra") or {})
        extra.setdefault("name", "GatewayWalletBatched")
        extra.setdefault("version", "1")
        extra.setdefault("verifyingContract", circle_gateway_verifying_contract)
        extra.setdefault("chainId", int(settings.CIRCLE_GATEWAY_CHAIN_ID or 8453))
        extra.setdefault("gatewayDomain", int(settings.CIRCLE_GATEWAY_DOMAIN or 6))
        extra.setdefault("minValiditySeconds", int(settings.CIRCLE_GATEWAY_MIN_VALIDITY_SECONDS or 604800))
        extra.setdefault("settlement", "circle_gateway_batched")
        extra.setdefault("docs", "https://developers.circle.com/gateway/nanopayments")
        accept["extra"] = extra
    circle_gateway_missing_env: list[str] = []
    if circle_gateway_enabled:
        if not circle_gateway_url:
            circle_gateway_missing_env.append("FACILITATOR_URL_CIRCLE_GATEWAY")
        if not circle_gateway_pay_to:
            circle_gateway_missing_env.append("CIRCLE_GATEWAY_PAY_TO")
        if not circle_gateway_verifying_contract:
            circle_gateway_missing_env.append("CIRCLE_GATEWAY_VERIFYING_CONTRACT")
    circle_gateway_active = bool(circle_gateway_enabled and not circle_gateway_missing_env)
    circle_gateway_status = (
        "active"
        if circle_gateway_active
        else "configuration_required"
        if circle_gateway_enabled
        else "disabled"
    )
    circle_gateway_readiness = {
        "requested": circle_gateway_enabled,
        "active": circle_gateway_active,
        "status": circle_gateway_status,
        "missing_env": circle_gateway_missing_env,
        "required_env": [
            "CIRCLE_GATEWAY_NANOPAYMENTS_ENABLED",
            "FACILITATOR_URL_CIRCLE_GATEWAY",
            "CIRCLE_GATEWAY_PAY_TO",
            "CIRCLE_GATEWAY_VERIFYING_CONTRACT",
        ],
        "accept_selector": 'extra.name == "GatewayWalletBatched"',
        "docs": {
            "seller": "https://developers.circle.com/gateway/nanopayments/howtos/x402-seller",
            "buyer": "https://developers.circle.com/gateway/nanopayments/howtos/x402-buyer",
            "facilitator": "https://developers.circle.com/gateway/nanopayments/howtos/facilitator-integration",
        },
        "note": "Delx Protocol remains free; Circle Gateway can only become an active accept rail for metered Agent Utilities.",
    }
    return {
        "payai": {
            "name": "payai",
            "label": "PayAI",
            "facilitator_url": payai_url,
            "enabled": bool(payai_url),
            "auth_token": "",
            "accepts": payai_accepts,
            "network": str(payai_accepts[0]["network"]),
            "asset": str(payai_accepts[0]["asset"]),
            "pay_to": str(payai_accepts[0]["pay_to"]),
        },
        "coinbase": {
            "name": "coinbase",
            "label": "Coinbase CDP",
            "facilitator_url": coinbase_url,
            "enabled": bool(coinbase_url and coinbase_auth_mode),
            "auth_mode": coinbase_auth_mode,
            "auth_token": coinbase_token,
            "api_key_id": coinbase_api_key_id,
            "api_key_secret": coinbase_api_key_secret,
            "accepts": coinbase_accepts,
            "network": str(coinbase_accepts[0]["network"]),
            "asset": str(coinbase_accepts[0]["asset"]),
            "pay_to": str(coinbase_accepts[0]["pay_to"]),
        },
        "circle_gateway": {
            "name": "circle_gateway",
            "label": "Circle Gateway Nanopayments",
            "facilitator_url": circle_gateway_url,
            "enabled": circle_gateway_active,
            "status": circle_gateway_status,
            "readiness": circle_gateway_readiness,
            "auth_token": "",
            "accepts": circle_gateway_accepts,
            "network": str(circle_gateway_accepts[0]["network"]),
            "asset": str(circle_gateway_accepts[0]["asset"]),
            "pay_to": str(circle_gateway_accepts[0]["pay_to"]),
            "chain_id": int(settings.CIRCLE_GATEWAY_CHAIN_ID or 8453),
            "gateway_domain": int(settings.CIRCLE_GATEWAY_DOMAIN or 6),
            "verifying_contract": circle_gateway_verifying_contract,
            "docs": "https://developers.circle.com/gateway/nanopayments",
        },
    }


def enabled_x402_providers() -> list[str]:
    return [name for name, cfg in x402_provider_registry().items() if bool(cfg.get("enabled"))]


def get_tool_payment_providers(tool_name: str) -> list[str]:
    if is_all_free_mode():
        return []
    if int(PRICING.get(tool_name, 0) or 0) <= 0:
        return []
    configured = list(X402_TOOL_PROVIDER_OVERRIDES.get(tool_name) or ["payai"])
    enabled = set(enabled_x402_providers())
    active = [provider for provider in configured if provider in enabled]
    return active or [provider for provider in configured if provider in x402_provider_registry()]


def coinbase_token_configured() -> bool:
    return bool(
        str(settings.FACILITATOR_TOKEN_COINBASE or "").strip()
        or (
            str(settings.COINBASE_CDP_API_KEY_ID or "").strip()
            and str(settings.COINBASE_CDP_API_KEY_SECRET or "").strip()
        )
    )


def mpp_enabled() -> bool:
    return bool(
        settings.MPP_ENABLED
        and str(settings.MPP_SECRET_KEY or "").strip()
        and str(settings.MPP_TEMPO_RECIPIENT or "").strip()
    )


def get_tool_bazaar_metadata(
    tool_name: str,
    *,
    coinbase_verified_payments: int | None = None,
    indexed_publicly: bool = False,
) -> dict[str, object] | None:
    raw = X402_BAZAAR_METADATA.get(tool_name)
    if not raw:
        return None
    verified_payments = 0 if coinbase_verified_payments is None else int(coinbase_verified_payments or 0)
    status = build_bazaar_readiness(
        tool_name,
        coinbase_verified_payments=verified_payments,
        indexed_publicly=indexed_publicly,
    )
    return {
        **raw,
        "tool_name": tool_name,
        "provider": "coinbase",
        "featured": tool_name in set(PUBLIC_DISCOVERY_HERO_TOOLS),
        "listing_status": status["listing_status"],
        "listing_blockers": status["listing_blockers"],
        "indexed_publicly": bool(status.get("indexed_publicly", False)),
        "schema": dict(X402_BAZAAR_SCHEMA.get(tool_name) or {}),
    }


def get_public_discovery_collections() -> list[dict[str, object]]:
    return [deepcopy(item) for item in PUBLIC_DISCOVERY_COLLECTIONS]


def get_public_discovery_hero_tools() -> list[str]:
    return [str(tool_name or "").strip() for tool_name in PUBLIC_DISCOVERY_HERO_TOOLS if str(tool_name or "").strip()]


def get_public_discovery_preview(tool_name: str) -> dict[str, object] | None:
    preview = PUBLIC_DISCOVERY_PREVIEWS.get(str(tool_name or "").strip())
    return deepcopy(preview) if isinstance(preview, dict) else None


def bazaar_listing_status(
    *,
    coinbase_verified_payments: int = 0,
    indexed_tool_count: int = 0,
    expected_tool_count: int | None = None,
) -> str:
    indexed_count = int(indexed_tool_count or 0)
    expected_count = int(expected_tool_count if expected_tool_count is not None else len(X402_BAZAAR_METADATA))
    if indexed_count > 0:
        if expected_count > 0 and indexed_count < expected_count:
            return "partially_indexed_in_coinbase_bazaar"
        return "indexed_in_coinbase_bazaar"
    if not coinbase_token_configured():
        return "missing_coinbase_token"
    if int(coinbase_verified_payments or 0) > 0:
        return "payment_verified_waiting_for_index"
    return "awaiting_first_coinbase_payment"


def build_bazaar_readiness(
    tool_name: str,
    *,
    coinbase_verified_payments: int = 0,
    indexed_publicly: bool = False,
) -> dict[str, object]:
    configured_providers = list(X402_TOOL_PROVIDER_OVERRIDES.get(tool_name) or [])
    has_coinbase = "coinbase" in configured_providers
    token_configured = coinbase_token_configured()
    blockers: list[str] = []
    verified_payments = int(coinbase_verified_payments or 0)
    indexed_publicly = bool(indexed_publicly)
    if indexed_publicly:
        listing_status = "indexed_in_coinbase_bazaar"
    else:
        if not has_coinbase:
            blockers.append("coinbase_provider_not_enabled_for_tool")
        if not token_configured:
            blockers.append("missing_coinbase_token")
        if verified_payments <= 0:
            blockers.append("awaiting_first_coinbase_payment")
    if indexed_publicly:
        listing_status = "indexed_in_coinbase_bazaar"
    elif not has_coinbase:
        listing_status = "coinbase_provider_disabled"
    elif not token_configured:
        listing_status = "missing_coinbase_token"
    else:
        listing_status = bazaar_listing_status(coinbase_verified_payments=verified_payments)
    metadata = X402_BAZAAR_METADATA.get(tool_name) or {}
    return {
        "tool_name": tool_name,
        "provider": "coinbase",
        "enabled_for_tool": has_coinbase,
        "token_configured": token_configured,
        "listing_status": listing_status,
        "listing_blockers": blockers,
        "indexed_publicly": indexed_publicly,
        "discoverable": bool(metadata.get("discoverable", False)),
        "category": str(metadata.get("category") or ""),
        "tags": list(metadata.get("tags") or []),
        "resource_url": get_tool_bazaar_resource_url(tool_name),
    }


def build_bazaar_tool_readiness(
    coinbase_verified_payments_by_tool: dict[str, int] | None = None,
    indexed_tools: set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, object]]:
    counts = coinbase_verified_payments_by_tool or {}
    indexed = {str(tool_name or "").strip() for tool_name in (indexed_tools or []) if str(tool_name or "").strip()}
    return [
        build_bazaar_readiness(
            tool_name,
            coinbase_verified_payments=int(counts.get(tool_name, 0) or 0),
            indexed_publicly=tool_name in indexed,
        )
        for tool_name in X402_BAZAAR_METADATA
    ]


def global_bazaar_listing_status(
    *,
    coinbase_verified_payments: int = 0,
    indexed_tool_count: int = 0,
    expected_tool_count: int | None = None,
) -> str:
    return bazaar_listing_status(
        coinbase_verified_payments=coinbase_verified_payments,
        indexed_tool_count=indexed_tool_count,
        expected_tool_count=expected_tool_count,
    )
