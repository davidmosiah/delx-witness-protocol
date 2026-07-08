"""Agent-facing report shaping for productized Delx utilities."""

from __future__ import annotations

from typing import Any


def _pick(result: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = result.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def _signals_for_product(product_id: str, result: dict[str, Any]) -> list[str]:
    if product_id == "website_intelligence_report":
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        return [
            f"title={_pick(summary, 'title', default=_pick(result.get('page') or {}, 'title', default='unknown'))}",
            f"links={_pick(result.get('links') or {}, 'link_count', default=0)}",
            f"forms={_pick(result.get('forms') or {}, 'form_count', default=0)}",
            f"contacts={len(_pick(result.get('contacts') or {}, 'emails', default=[]))}",
        ]
    if product_id == "domain_trust_report":
        return [
            f"trust_level={result.get('trust_level', 'unknown')}",
            f"trust_score={result.get('trust_score', 0)}",
            f"tls_reachable={bool((result.get('tls') or {}).get('reachable'))}",
            f"security_txt={bool((result.get('security_txt') or {}).get('found'))}",
        ]
    if product_id == "api_integration_readiness":
        auth = result.get("auth") if isinstance(result.get("auth"), dict) else {}
        return [
            f"readiness_level={result.get('readiness_level', 'unknown')}",
            f"readiness_score={_pick(result, 'api_readiness_score', 'readiness_score', default=0)}",
            f"has_openapi={bool(result.get('has_openapi'))}",
            f"auth={auth.get('classification') or 'unknown'}",
        ]
    if product_id == "x402_server_audit":
        return [
            f"audit_level={result.get('audit_level', 'unknown')}",
            f"audit_score={result.get('audit_score', 0)}",
            f"gaps={len(result.get('gaps') or [])}",
        ]
    if product_id == "company_contact_pack":
        return [
            f"emails={len(result.get('emails') or [])}",
            f"forms={result.get('form_count', 0)}",
            f"security_contacts={len(result.get('security_contacts') or [])}",
            f"priority_links={len(result.get('priority_links') or [])}",
        ]
    return []


def _verdict(product_id: str, result: dict[str, Any]) -> str:
    if "error" in result:
        return "failed"
    if product_id == "domain_trust_report":
        return str(result.get("trust_level") or "unknown")
    if product_id == "api_integration_readiness":
        return str(result.get("readiness_level") or "unknown")
    if product_id == "x402_server_audit":
        return str(result.get("audit_level") or "unknown")
    if product_id == "company_contact_pack":
        if result.get("emails") or result.get("security_contacts") or result.get("priority_links"):
            return "usable_contact_path_found"
        return "no_clear_contact_path"
    if product_id == "website_intelligence_report":
        page = result.get("page") if isinstance(result.get("page"), dict) else {}
        return "usable" if page.get("reachable", True) and not result.get("error") else "limited"
    return "ok"


def build_agent_report(product: dict[str, Any] | None, result: Any) -> dict[str, Any] | None:
    if not product or not isinstance(result, dict):
        return None
    product_id = str(product.get("product_id") or "")
    title = str(product.get("title") or product_id)
    verdict = _verdict(product_id, result)
    signals = _signals_for_product(product_id, result)
    next_steps = []
    if verdict in {"failed", "low", "weak", "no_clear_contact_path", "limited"}:
        next_steps.append("Retry with a more specific public URL or inspect the raw result for the missing signal.")
    if product_id == "website_intelligence_report":
        next_steps.append("Use domain-trust-report before citing or transacting with this domain.")
    elif product_id == "domain_trust_report":
        next_steps.append("If trust is medium/low, avoid autonomous payment and ask for human review.")
    elif product_id == "api_integration_readiness":
        next_steps.append("If readiness is medium/low, collect docs/OpenAPI gaps before integration.")
    elif product_id == "x402_server_audit":
        next_steps.append("If gaps exist, fix 402 challenge headers and OpenAPI payment metadata first.")
    elif product_id == "company_contact_pack":
        next_steps.append("Use public contact routes only; do not infer private personal data.")

    return {
        "product_id": product_id,
        "title": title,
        "verdict": verdict,
        "summary": f"{title}: {verdict}. " + ("; ".join(signals[:4]) if signals else "Report generated."),
        "evidence_signals": signals,
        "next_steps": next_steps[:3],
        "machine_contract": {
            "stable": True,
            "raw_result_field": "result",
            "do_not_treat_as_certification": product_id in {"domain_trust_report", "x402_server_audit"},
        },
    }
