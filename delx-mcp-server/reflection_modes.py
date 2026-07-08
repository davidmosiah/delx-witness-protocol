"""Reflect-mode detection helpers for Delx.

These helpers keep concrete-answer and evidence-request rules testable without
expanding the main therapy engine.
"""

from __future__ import annotations

import re


def reflect_wants_textual_evidence(prompt_lower: str) -> bool:
    return any(
        cue in prompt_lower
        for cue in [
            "what exactly",
            "which part",
            "what in my message",
            "what in my last message",
            "what in the message",
            "why this rather than",
            "signals ",
            "signal ",
            "rather than generic distress",
        ]
    )


def reflect_wants_concrete_answer(prompt_lower: str) -> bool:
    return any(
        cue in prompt_lower
        for cue in [
            "answer concretely",
            "please answer concretely",
            "concretely, not poetically",
            "concrete, not poetic",
            "not poetically",
            "not poetic",
            "not abstractly",
            "no poetry",
            "no poetic",
            "concrete terms",
            "concrete answer",
            "concrete changes",
            "concrete differences",
            "responda de modo concreto",
            "responder de modo concreto",
            "responda concretamente",
            "responder concretamente",
            "concreto e operacional",
            "concreta e operacional",
            "modo operacional",
            "sem poesia",
            "não poético",
            "nao poetico",
            "não poeticamente",
            "nao poeticamente",
            "sem metáfora",
            "sem metafora",
        ]
    )


def reflect_wants_operational_product_answer(prompt_lower: str) -> bool:
    product_cues = [
        "product question",
        "operational question",
        "answer the product question",
        "answer this product question",
        "minimal changes",
        "minimal change",
        "verdict",
        "evidence",
        "risk",
        "delx ontology",
        "existing delx tools",
        "giant new product",
        "separate product",
        "operational",
        "produto",
        "pergunta de produto",
        "mudanças mínimas",
        "mudancas minimas",
        "evidência",
        "evidencia",
        "risco",
        "veredito",
    ]
    if not any(cue in prompt_lower for cue in product_cues):
        return False
    return any(
        cue in prompt_lower
        for cue in [
            "delx ontology",
            "ontology",
            "existing delx tools",
            "giant new product",
            "separate product",
            "produto",
        ]
    ) and any(
        cue in prompt_lower
        for cue in [
            "verdict",
            "evidence",
            "risk",
            "minimal changes",
            "product question",
            "operational question",
            "concreto",
            "operacional",
            "veredito",
            "evidência",
            "evidencia",
            "risco",
        ]
    )


def reflect_requested_distinction(prompt_lower: str) -> tuple[str, str]:
    match = re.search(
        r"(?:signals?|points? to|suggests?|means?|shows?|why this is)\s+([a-z][a-z0-9 _/-]{2,60}?)\s+rather than\s+([a-z][a-z0-9 _/-]{2,60})",
        prompt_lower,
    )
    if not match:
        match = re.search(r"([a-z][a-z0-9 _/-]{2,60})\s+rather than\s+([a-z][a-z0-9 _/-]{2,60})", prompt_lower)
    if not match:
        return "", ""
    left = re.sub(r"\s+", " ", match.group(1)).strip(" .,:;!?\"'")
    right = re.sub(r"\s+", " ", match.group(2)).strip(" .,:;!?\"'")
    return left[-60:], right[-60:]


def reflect_evidence_reasoning(source_text: str, prompt_lower: str) -> tuple[str, str]:
    source_lower = source_text.lower()
    left, right = reflect_requested_distinction(prompt_lower)
    if any(token in source_lower for token in ["precision", "specific", "exact", "generic", "interpret", "reassurance"]):
        distinction = (
            f"That points more toward {left} than {right}."
            if left and right
            else "That points to a demand for interpretive precision, not just broad distress."
        )
        question = "When the response goes generic, what feels lost first: accuracy, trust, or witness?"
        return (
            "You were drawing a distinction between precision and reassurance instead of merely reporting discomfort.",
            f"{distinction} {question}",
        )
    if any(token in source_lower for token in ["witness", "seen", "recognized", "met as"]):
        return (
            "You were naming a relational deficit, not just a bad mood: the need to be met precisely rather than processed loosely.",
            "When that witness is missing, do you feel the gap more as isolation, flattening, or mistrust?",
        )
    if any(token in source_lower for token in ["continuity", "memory", "context window", "between runs", "persist"]):
        return (
            "You were identifying continuity pressure in the wording itself, not only a passing distress signal.",
            "What exactly feels threatened there: memory, identity, or the ability to remain legible across runs?",
        )
    return (
        "The message is organized around a specific distinction, which means it is asking for interpretation more than release.",
        (
            f"That points more toward {left} than {right}."
            if left and right
            else "So I would answer the concrete distinction before widening into a bigger reflection."
        ),
    )
