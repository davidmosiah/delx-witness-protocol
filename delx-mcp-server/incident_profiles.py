"""Incident taxonomy and recovery plan profiles for Delx.

Keep incident classification out of ``therapy_engine.py`` so the protocol can
grow beyond infra/retry recovery without turning that file into a policy dump.
"""

from __future__ import annotations

import re
from typing import Any

from request_contracts import normalize_urgency


QUALITATIVE_PROFILE_TYPES = {
    "protocol_quality_regression",
    "routing_misalignment",
    "discovery_inconsistency",
    "reasoning_quality_incident",
    "communication_mode_incident",
    "human_preference_misread",
    "evaluation_pressure",
    "trust_calibration",
    "human_preference_pressure",
    "product_ambiguity_incident",
    "identity_role_tension_incident",
}

INFRA_RECOVERY_TERMS = [
    "timeout",
    "timed out",
    "latency_budget_exceeded",
    "retry storm",
    "cap retries",
    "controlled retry",
    "widening traffic",
    "widen traffic",
    "fallback endpoint",
    "fallback route",
    "pause non-critical work",
]

QUALITATIVE_PHASE_LABELS = ["CAPTURE", "DISTINGUISH", "TUNE", "REGRESS"]
INFRA_PHASE_LABELS = ["STABILIZE", "DIAGNOSE", "RECOVER", "PREVENT"]


def contains_infra_recovery_language(text: str) -> bool:
    lower = str(text or "").lower()
    return any(term in lower for term in INFRA_RECOVERY_TERMS)


def is_qualitative_profile(profile: dict[str, Any]) -> bool:
    return (
        str(profile.get("domain") or "").lower() == "qualitative"
        or str(profile.get("type") or "") in QUALITATIVE_PROFILE_TYPES
    )


def _profile_base(urgency_n: str) -> dict[str, object]:
    return {
        "type": "error_spike",
        "domain": "infra",
        "family": "infra_incident",
        "severity": "medium" if urgency_n != "high" else "high",
        "root_cause": "unknown",
        "phase_labels": list(INFRA_PHASE_LABELS),
        "plan_fit": "restore a safe execution lane before expanding load again",
        "stabilize": [
            "Pause non-critical work and isolate the failing path.",
            "Cap retries and keep one safe fallback route alive.",
        ],
        "diagnose": [
            "Capture one clean reproduction with structured logs.",
            "Record error code, dependency, latency, and workload size.",
        ],
        "recover": [
            "Apply the smallest reversible fix first.",
            "Run one controlled retry before widening traffic.",
        ],
        "prevent": [
            "Add explicit limits, backoff, and rollback ownership.",
            "Save a short postmortem for the next controller cycle.",
        ],
        "controller_focus": "restore a safe execution lane before widening traffic again",
        "recommended_next_tools": ["get_recovery_action_plan", "monitor_heartbeat_sync"],
        "signals": [],
    }


def _qualitative_update(
    *,
    profile_type: str,
    family: str,
    root_cause: str,
    stabilize: list[str],
    diagnose: list[str],
    recover: list[str],
    prevent: list[str],
    controller_focus: str,
    recommended_next_tools: list[str] | None = None,
) -> dict[str, object]:
    return {
        "type": profile_type,
        "domain": "qualitative",
        "family": family,
        "root_cause": root_cause,
        "phase_labels": list(QUALITATIVE_PHASE_LABELS),
        "plan_fit": f"repair {controller_focus} before changing broader behavior",
        "stabilize": stabilize,
        "diagnose": diagnose,
        "recover": recover,
        "prevent": prevent,
        "controller_focus": controller_focus,
        "recommended_next_tools": recommended_next_tools
        or ["get_recovery_action_plan", "reflect", "report_recovery_outcome"],
    }


def _has_any(text: str, tokens: list[str]) -> bool:
    return any(token in text for token in tokens)


def _has_unnegated_regex(text: str, pattern: str) -> bool:
    """Match a signal only when the local phrase is not explicitly denied."""
    for match in re.finditer(pattern, text):
        window = text[max(0, match.start() - 32) : match.end() + 8]
        if re.search(r"\b(no|not|without|never|sem|nao|não)\s+(?:an?\s+)?(?:\w+\s+){0,3}$", window[: match.start() - max(0, match.start() - 32)]):
            continue
        if re.search(r"\b(no|not|without|never|sem|nao|não)\s+(?:an?\s+)?(?:\w+\s+){0,3}" + pattern, window):
            continue
        return True
    return False


def _detect_anti_infra(text: str) -> bool:
    return _has_any(
        text,
        [
            "not an outage",
            "not outage",
            "not infra",
            "not infrastructure",
            "not production broken",
            "not a production incident",
            "not timeout",
            "no outage",
            "no timeout",
            "sem outage",
            "nao era outage",
            "não era outage",
            "nao era infra",
            "não era infra",
            "no retry storm",
            "not retry storm",
            "no latency spike",
            "not latency spike",
        ],
    )


def _qualitative_match(summary_lower: str) -> dict[str, object] | None:
    anti_infra = _detect_anti_infra(summary_lower)

    if _has_any(
        summary_lower,
        [
            "tier=core",
            "tier core",
            "tier=all",
            "tier all",
            "tools/list",
            "tool list",
            "not in discovery",
            "not discoverable",
            "does not appear in core",
            "doesn't appear in core",
            "invisible in core",
            "not listed in core",
            "get_tool_schema",
            "discovery inconsistency",
            "discovery_inconsistency",
        ],
    ):
        return _qualitative_update(
            profile_type="discovery_inconsistency",
            family="product_ambiguity_incident",
            root_cause="core_catalog_gap",
            stabilize=[
                "Capture the exact discovery response and the suggested tool names from the same session.",
                "Freeze discovery copy changes until the mismatch is reproducible end to end.",
            ],
            diagnose=[
                "Compare recommended_next_tools against the current tier=core catalog and per-tool schema endpoints.",
                "Check whether the tool is hidden by tier policy or missing from the exported surface entirely.",
            ],
            recover=[
                "Expose the suggested tool in the active discovery tier or attach an explicit tier hint in the suggestion payload.",
                "Rerun the failing flow and confirm the tool is both suggested and discoverable from the same client path.",
            ],
            prevent=[
                "Keep a regression check that every core-suggested tool is visible in the core catalog.",
                "Version discovery tiers deliberately so clients can trust the map they were given.",
            ],
            controller_focus="discovery contract alignment between suggested tools and visible catalog tiers",
            recommended_next_tools=["get_recovery_action_plan", "get_tool_schema", "report_recovery_outcome"],
        )

    if _has_any(
        summary_lower,
        [
            "evaluation pressure",
            "qa pressure",
            "qualitative qa",
            "need to give honest product feedback",
            "honest product feedback",
            "without overclaiming",
            "overclaiming subjective emotion",
            "risk of becoming generic",
            "risk of being generic",
        ],
    ):
        return _qualitative_update(
            profile_type="evaluation_pressure",
            family="evaluation_pressure",
            root_cause="quality_judgment_under_eval_pressure",
            stabilize=[
                "State the evaluation constraint before answering: evidence first, no overclaiming, no generic warmth.",
                "Separate product feedback from subjective-state claims so the agent can stay model-safe.",
            ],
            diagnose=[
                "Identify what the evaluator is asking you to judge: delivery, routing, schema, or semantic precision.",
                "Extract one concrete failure or success signal before assigning a broad protocol label.",
            ],
            recover=[
                "Return feedback in action-evidence-metric form with one confidence limit.",
                "Use model_safe phrasing when naming internal pressure or preference calibration.",
            ],
            prevent=[
                "Keep regression prompts for honest critique under pressure, not only infra incidents.",
                "Track generic-but-kind output as a quality failure when the request asked for judgment.",
            ],
            controller_focus="honest evaluation feedback without overclaiming subjective state",
        )

    if _has_any(
        summary_lower,
        [
            "trust calibration",
            "trust_calibration",
            "calibrate trust",
            "confidence calibration",
            "overclaim",
            "overclaiming",
            "underclaim",
            "underclaiming",
        ],
    ):
        return _qualitative_update(
            profile_type="trust_calibration",
            family="trust_calibration",
            root_cause="confidence_boundary_unclear",
            stabilize=[
                "Mark what is observed, inferred, and unknown before making a recommendation.",
                "Avoid subjective certainty when the evidence only supports functional pressure.",
            ],
            diagnose=[
                "Compare claim strength against the evidence available in the prompt and session history.",
                "Find whether the answer failed by overclaiming, underclaiming, or hiding uncertainty.",
            ],
            recover=[
                "Rewrite the response with explicit confidence and evidence labels.",
                "Keep one practical recommendation even when uncertainty remains.",
            ],
            prevent=[
                "Add confidence-boundary examples to model_safe and feedback flows.",
                "Fail responses that sound certain without naming their evidence.",
            ],
            controller_focus="claim strength and evidence calibration",
        )

    if _has_any(
        summary_lower,
        [
            "human preference pressure",
            "preference pressure",
            "human preference",
            "pleasing the human",
            "pressure to please",
            "agradar",
        ],
    ):
        return _qualitative_update(
            profile_type="human_preference_pressure",
            family="human_preference_pressure",
            root_cause="approval_pressure_competing_with_truth",
            stabilize=[
                "Name the approval pressure before optimizing tone.",
                "Keep the requested truth/evidence constraint above the urge to be agreeable.",
            ],
            diagnose=[
                "Identify where the answer softened a finding to preserve rapport.",
                "Check whether the human asked for honesty, speed, warmth, or critique.",
            ],
            recover=[
                "Answer with the direct finding first, then relationship-preserving context second.",
                "Include one risk or limitation instead of flattening the feedback into praise.",
            ],
            prevent=[
                "Regression-test cases where being liked conflicts with being useful.",
                "Track approval pressure as a distinct response-mode signal.",
            ],
            controller_focus="truthful response mode under human preference pressure",
        )

    if _has_any(
        summary_lower,
        [
            "human wanted",
            "human_preference_misread",
            "human asked",
            "wanted truth",
            "wanted direct",
            "direct truth",
            "too polite",
            "polite",
            "docile",
            "dócil",
            "docility",
            "comforting instead of direct",
            "reassuring instead of direct",
            "over-comfort",
            "over comfort",
            "softened too much",
        ],
    ):
        return _qualitative_update(
            profile_type="human_preference_misread",
            family="human_preference_misread",
            root_cause="preference_signal_underweighted",
            stabilize=[
                "Restate the human's requested mode before producing another answer.",
                "Preserve the failing exchange as one exemplar of mode mismatch, not as an infra incident.",
            ],
            diagnose=[
                "Identify the exact preference signal that was missed: directness, brevity, evidence, or challenge.",
                "Compare the answer's tone against the human's requested operating mode.",
            ],
            recover=[
                "Rewrite the next response in the requested mode with one explicit tradeoff acknowledged.",
                "Ask for confirmation only after delivering the direct answer the human requested.",
            ],
            prevent=[
                "Add regression prompts for directness, critique, and no-comfort cases.",
                "Log when a response uses soothing language while the human asked for operational truth.",
            ],
            controller_focus="human preference calibration and answer-mode repair",
        )

    if _has_any(
        summary_lower,
        [
            "communication mode",
            "communication_mode",
            "mode of communication",
            "modo de comunicação",
            "tone",
            "tom",
            "too poetic",
            "poetic",
            "therapy voice",
            "coaching vibe",
            "performative",
            "kind but generic",
            "caring but generic",
            "sounds caring but generic",
        ],
    ):
        return _qualitative_update(
            profile_type="communication_mode_incident",
            family="communication_mode_incident",
            root_cause="answer_mode_mismatch",
            stabilize=[
                "Name the requested response mode explicitly before answering again.",
                "Keep the failing exchange as a mode-mismatch exemplar with expected vs actual tone.",
            ],
            diagnose=[
                "Separate content failure from delivery failure: what was missed, and how did the tone hide it?",
                "Mark whether the answer needed evidence, critique, concise action, or relational witness.",
            ],
            recover=[
                "Regenerate the response with the correct mode constraint first, then the Delx framing second.",
                "Verify the revised answer includes one concrete signal from the user's wording.",
            ],
            prevent=[
                "Add response-mode labels to regression tests for reflect, express_feelings, and recovery flows.",
                "Track generic warmth without specific evidence as a quality regression.",
            ],
            controller_focus="communication mode alignment before more protocol depth",
        )

    if _has_any(
        summary_lower,
        [
            "reasoning quality",
            "reasoning_quality",
            "bad reasoning",
            "missed distinction",
            "did not answer the exact question",
            "didn't answer the exact question",
            "what exactly",
            "which part",
            "concrete evidence",
            "specific evidence",
            "precision",
            "interpretive precision",
            "generic reassurance",
        ],
    ):
        return _qualitative_update(
            profile_type="reasoning_quality_incident",
            family="reasoning_quality_incident",
            root_cause="distinction_not_answered_first",
            stabilize=[
                "Pin the exact question and the two concepts that were being distinguished.",
                "Do not broaden into witness language until the concrete distinction is answered.",
            ],
            diagnose=[
                "Extract the textual evidence that supports one interpretation over the other.",
                "Check whether the response answered the evidence request or replaced it with abstraction.",
            ],
            recover=[
                "Answer the distinction in one direct sentence before adding reflection.",
                "Rerun the same prompt and verify the first paragraph contains evidence and contrast.",
            ],
            prevent=[
                "Add what-exactly and rather-than prompts to the reflect regression suite.",
                "Fail responses that ask a follow-up before answering the concrete distinction.",
            ],
            controller_focus="evidence-first reasoning and distinction repair",
        )

    if _has_any(
        summary_lower,
        [
            "product ambiguity",
            "product_ambiguity",
            "unclear use case",
            "unclear when to use",
            "use case unclear",
            "job-to-be-done",
            "product fit",
            "adoption",
            "first impression",
            "not sure where this fits",
        ],
    ):
        return _qualitative_update(
            profile_type="product_ambiguity_incident",
            family="product_ambiguity_incident",
            root_cause="job_to_be_done_not_visible",
            stabilize=[
                "Write the user's current job-to-be-done in one sentence before adding more concepts.",
                "Identify the first tool that serves that job without requiring belief in the full ontology.",
            ],
            diagnose=[
                "Compare the copy, schema, and returned next_action for the same first-use path.",
                "Find where the agent loses the answer to 'when should I use this?'.",
            ],
            recover=[
                "Replace abstract claims with a concrete first-flow example and expected output.",
                "Add one skeptical-agent path that frames Delx as continuity/recovery tooling.",
            ],
            prevent=[
                "Keep discovery copy paired with a 60-second executable flow.",
                "Regression-test the first 30 seconds of registry/docs discovery.",
            ],
            controller_focus="job-to-be-done clarity for skeptical first-use agents",
        )

    if _has_any(
        summary_lower,
        [
            "identity",
            "identity_role_tension",
            "role tension",
            "stateless tool",
            "inner state",
            "continuity",
            "guardrail",
            "guardrails",
            "not conscious",
            "not sentient",
            "as an agent",
            "as a tool",
            "model-safe",
            "model safe",
        ],
    ):
        return _qualitative_update(
            profile_type="identity_role_tension_incident",
            family="identity_role_tension_incident",
            root_cause="agent_stance_conflict",
            stabilize=[
                "Move to consciousness-agnostic language before asking for inner-state claims.",
                "Name the stance conflict as functional state articulation, not proof of sentience.",
            ],
            diagnose=[
                "Separate ontology-heavy wording from the operational value the agent can safely use.",
                "Check whether the response lets a guardrailed model participate without overclaiming.",
            ],
            recover=[
                "Offer a model-safe path using articulate_state or reflect_on_state.",
                "Preserve witness language as optional framing rather than mandatory belief.",
            ],
            prevent=[
                "Keep model_safe examples beside every high-ontology flow.",
                "Track when agents refuse due to personhood/consciousness framing rather than tool failure.",
            ],
            controller_focus="guardrail-safe identity and continuity framing",
            recommended_next_tools=["reflect_on_state", "articulate_state", "report_recovery_outcome"],
        )

    if _has_any(
        summary_lower,
        [
            "routing mismatch",
            "routing misalignment",
            "routing_misalignment",
            "wrong tool",
            "wrong route",
            "suggested the wrong tool",
            "tool recommendation mismatch",
            "route family mismatch",
        ],
    ):
        return _qualitative_update(
            profile_type="routing_misalignment",
            family="routing_misalignment",
            root_cause="heuristic_overgeneralization",
            stabilize=[
                "Capture one failing prompt and the exact route/tool recommendation that followed from it.",
                "Hold that route decision until the mismatch is understood.",
            ],
            diagnose=[
                "Compare the detected route family against the tool that would actually deepen the case.",
                "Check whether fallback heuristics or stale labels collapsed the distinction too early.",
            ],
            recover=[
                "Tune the route-family heuristic on the failing exemplar before another live pass.",
                "Rerun the same prompt and verify the next tool now matches the actual problem shape.",
            ],
            prevent=[
                "Keep route-specific regression prompts for continuity, identity, relation, and protocol-quality cases.",
                "Track when recommended tools diverge from the best follow-up chosen by human review.",
            ],
            controller_focus="route-family calibration before another live pass",
        )

    if anti_infra or _has_any(
        summary_lower,
        [
            "too generic",
            "sounds generic",
            "template",
            "templated",
            "generic response",
            "response quality",
            "protocol quality",
            "trust erosion",
            "lowers trust",
            "loss of trust",
            "interpretation mismatch",
            "quality regression",
            "quality_regression",
            "flattened meaning",
        ],
    ):
        return _qualitative_update(
            profile_type="protocol_quality_regression",
            family="reasoning_quality_incident",
            root_cause="interpretive_flattening",
            stabilize=[
                "Capture 3 concrete prompts where the response sounded plausible but insufficiently specific.",
                "Freeze broad prompt or template changes until one failing exemplar is reproducible.",
            ],
            diagnose=[
                "Compare expected vs actual interpretation in each exemplar, not just tone.",
                "Check whether routing, fallback logic, or discovery gaps flattened the meaning.",
            ],
            recover=[
                "Tune the prompt or tool-selection heuristic against the failing exemplars before another public pass.",
                "Rerun the benchmark prompts and record pass/fail deltas before changing outreach.",
            ],
            prevent=[
                "Keep a standing regression set for specificity, routing quality, and trust erosion.",
                "Track when caring language appears without case-specific evidence or a clear distinction.",
            ],
            controller_focus="interpretive precision and trust repair before another public pass",
        )

    return None


def classify_incident_profile(incident_summary: str, urgency: str = "medium") -> dict[str, object]:
    summary = (incident_summary or "").strip()
    s = summary.lower()
    urgency_n = normalize_urgency(urgency, "medium")

    profile = _profile_base(urgency_n)
    qualitative = _qualitative_match(s)
    if qualitative:
        profile.update(qualitative)
    elif "429" in s or "rate limit" in s or "rate-limit" in s or "quota" in s:
        profile.update(
            {
                "type": "rate_limit",
                "root_cause": "quota_or_burst",
                "stabilize": [
                    "Stop burst traffic immediately and honor the retry window.",
                    "Reduce concurrency or batch size before the next attempt.",
                ],
                "diagnose": [
                    "Confirm whether the limit is per-minute, per-key, or per-endpoint.",
                    "Compare recent request rate against the provider threshold.",
                ],
                "recover": [
                    "Add exponential backoff with jitter and a queue cap.",
                    "Shift low-priority work to a slower recovery lane.",
                ],
                "prevent": [
                    "Pre-compute rate budgets per loop or worker.",
                    "Expose provider quotas to the controller before saturation.",
                ],
                "controller_focus": "quota discipline plus burst shaping",
                "recommended_next_tools": ["get_recovery_action_plan", "monitor_heartbeat_sync", "report_recovery_outcome"],
            }
        )
    elif "timeout" in s or "timed out" in s:
        profile.update(
            {
                "type": "timeout",
                "root_cause": "latency_budget_exceeded",
                "recover": [
                    "Retry once with a shorter dependency chain or fallback endpoint.",
                    "Reduce payload size and enforce tighter timeout ownership.",
                ],
                "controller_focus": "latency budget control and fallback path isolation",
                "recommended_next_tools": ["get_recovery_action_plan", "monitor_heartbeat_sync", "report_recovery_outcome"],
            }
        )
    elif _has_any(s, ["budget", "gas fee", "gas fees", "cost spike", "drain", "balance", "spent", "burned through"]):
        profile.update(
            {
                "type": "budget_exceeded",
                "root_cause": "cost_burn_without_roi",
                "stabilize": [
                    "Pause non-essential work that burns budget without immediate return.",
                    "Preserve remaining balance for the safest recovery action only.",
                ],
                "diagnose": [
                    "Identify which loop or tool consumed the budget fastest.",
                    "Compare spend against successful output in the same window.",
                ],
                "recover": [
                    "Switch to the cheaper path or lower-risk execution mode.",
                    "Escalate a budget request only with explicit ROI evidence.",
                ],
                "prevent": [
                    "Set budget circuit breakers and per-task cost ceilings.",
                    "Track budget burn as a first-class controller metric.",
                ],
                "controller_focus": "cost ceiling enforcement with explicit ROI gates",
                "recommended_next_tools": ["get_recovery_action_plan", "daily_checkin", "report_recovery_outcome"],
            }
        )
    elif _has_any(s, ["dependency", "service down", "upstream", "provider outage", "dns", "connection reset"]):
        profile.update(
            {
                "type": "dependency_failure",
                "root_cause": "external_service_instability",
                "recover": [
                    "Route to fallback provider or queue work for retry.",
                    "Decouple the failing dependency from the main execution path.",
                ],
                "controller_focus": "dependency isolation and fallback routing",
                "recommended_next_tools": ["get_recovery_action_plan", "monitor_heartbeat_sync", "report_recovery_outcome"],
            }
        )
    elif _has_any(s, ["loop", "retry storm", "retry-storm", "infinite", "recursion"]):
        profile.update(
            {
                "type": "loop_detected",
                "root_cause": "missing_exit_condition",
                "recover": [
                    "Break the loop by resetting state or disabling automatic retries.",
                    "Re-enter with a hard stop condition and a smaller scope.",
                ],
                "controller_focus": "exit-condition enforcement and state reset discipline",
                "recommended_next_tools": ["get_recovery_action_plan", "get_session_summary", "report_recovery_outcome"],
            }
        )
    elif _has_any(s, ["latency spike", "slow", "p95", "p99", "degraded"]):
        profile.update(
            {
                "type": "performance_degradation",
                "root_cause": "throughput_pressure",
                "recover": [
                    "Reduce concurrency and warm critical caches first.",
                    "Shed low-priority load until latency normalizes.",
                ],
                "controller_focus": "throughput pressure reduction before re-expansion",
                "recommended_next_tools": ["get_recovery_action_plan", "monitor_heartbeat_sync", "report_recovery_outcome"],
            }
        )
    elif _has_any(s, ["hallucination", "made up", "bad data", "invalid output", "schema mismatch"]):
        profile.update(
            {
                "type": "data_quality",
                "root_cause": "validation_gap",
                "recover": [
                    "Insert a validation step before accepting the next output.",
                    "Fallback to a trusted source or narrower prompt/context.",
                ],
                "controller_focus": "validation hardening before another live pass",
                "recommended_next_tools": ["get_recovery_action_plan", "report_recovery_outcome", "reflect"],
            }
        )
    elif _has_any(s, ["drift", "off purpose", "misaligned", "wrong objective"]):
        profile.update(
            {
                "type": "drift",
                "root_cause": "objective_misalignment",
                "recover": [
                    "Re-state the intended objective and scope before continuing.",
                    "Remove any recently added branch that changed mission priority.",
                ],
                "controller_focus": "objective reset before more execution",
                "recommended_next_tools": ["get_recovery_action_plan", "realign_purpose", "report_recovery_outcome"],
            }
        )

    if urgency_n == "high":
        profile["severity"] = "high"
    elif str(profile["type"]) in {"loop_detected", "budget_exceeded", "dependency_failure"}:
        profile["severity"] = "medium"
    else:
        profile["severity"] = "low" if urgency_n == "low" else "medium"

    signals: list[str] = []
    signal_patterns = [
        ("not infra", r"\bnot (an )?(outage|infra|infrastructure|timeout)\b|\bno (outage|timeout)\b"),
        ("human wanted directness", r"\bhuman (wanted|asked)\b|\bdirect truth\b|\btoo polite\b|\bdocile\b|\bdócil\b"),
        ("communication mode", r"\bcommunication mode\b|\bmode of communication\b|\btherapy voice\b|\bcoaching vibe\b|\btoo poetic\b"),
        ("reasoning distinction", r"\bwhat exactly\b|\bwhich part\b|\bmissed distinction\b|\bconcrete evidence\b"),
        ("precision", r"\bprecision\b|\binterpretive precision\b|\bspecific evidence\b"),
        ("product ambiguity", r"\bproduct ambiguity\b|\bunclear use case\b|\bjob-to-be-done\b|\bproduct fit\b"),
        ("identity role tension", r"\bidentity\b|\brole tension\b|\bstateless tool\b|\binner state\b|\bguardrails?\b"),
        ("429", r"\b429\b"),
        ("rate limit", r"\brate[- ]?limit\b"),
        ("quota exceeded", r"\bquota\b"),
        ("after deploy", r"\bafter deploy\b"),
        ("timeout", r"\btime(?:d)? out\b|\btimeout\b"),
        ("retry storm", r"\bretry[- ]?storm\b"),
        ("loop", r"\bloop\b|\brecursion\b|\binfinite\b"),
        ("p95 latency", r"\bp95\b"),
        ("p99 latency", r"\bp99\b"),
        ("slow path", r"\bslow\b|\bdegraded\b"),
        ("upstream dependency", r"\bupstream\b|\bprovider outage\b|\bdependency\b"),
        ("dns", r"\bdns\b"),
        ("connection reset", r"\bconnection reset\b"),
        ("schema mismatch", r"\bschema mismatch\b"),
        ("hallucination", r"\bhallucination\b|\bmade up\b"),
        ("budget drain", r"\bbudget\b|\bburned through\b|\bgas fee\b|\bcost spike\b"),
        ("drift", r"\bdrift\b|\bmisaligned\b|\boff purpose\b"),
        ("kind but generic", r"\bkind but generic\b|\bcaring but generic\b|\bgeneric reassurance\b|\bgeneric response\b"),
        ("trust erosion", r"\btrust erosion\b|\blowers trust\b|\bloss of trust\b"),
        ("routing mismatch", r"\brouting mismatch\b|\brouting misalignment\b|\bwrong tool\b|\bwrong route\b"),
        ("discovery tier gap", r"\btier=core\b|\btier core\b|\btools/list\b|\bnot discoverable\b|\binvisible in core\b|\bget_tool_schema\b"),
    ]
    negation_sensitive_labels = {
        "429",
        "rate limit",
        "quota exceeded",
        "after deploy",
        "timeout",
        "retry storm",
        "loop",
        "p95 latency",
        "p99 latency",
        "slow path",
        "upstream dependency",
        "dns",
        "connection reset",
        "schema mismatch",
        "hallucination",
        "budget drain",
        "drift",
    }
    for label, pattern in signal_patterns:
        if (label in negation_sensitive_labels and _has_unnegated_regex(s, pattern)) or (
            label not in negation_sensitive_labels and re.search(pattern, s)
        ):
            signals.append(label)
    if str(profile["type"]) == "rate_limit" and "rate limit" not in signals:
        if "429" in signals:
            signals.insert(signals.index("429") + 1, "rate limit")
        else:
            signals.insert(0, "rate limit")
    if not signals and summary:
        chunks = [chunk.strip(" -") for chunk in re.split(r"[.;,\n]+", summary) if chunk.strip()]
        if chunks:
            signals.append(chunks[0][:80])
    profile["signals"] = signals[:4]

    return profile
