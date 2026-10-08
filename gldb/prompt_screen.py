"""
gldb/prompt_screen.py
---------------------
Prompt attack screening: each page's text is checked by a Bedrock guardrail
(prompt attack filter: jailbreak and prompt injection) before any model reads
it.

The guardrail is used as a detector through the ApplyGuardrail API, not as a
silent block on model calls: a flagged page becomes a PROMPT_INJECTION
finding (a hard flag), so an analyst sees that a document tried to instruct
the system. The other defences still apply whatever the screen says: hidden
text never reaches a model, document text is marked untrusted, output is
schema-bound and quotes are verified, and rules, not the model, set the
score and route.

The guardrail and its pinned version are defined in infra/guardrail.yaml.
"""

from __future__ import annotations

from functools import cache
from typing import Any

import boto3

from gldb.config import AWS_REGION, GUARDRAIL_ID, GUARDRAIL_VERSION
from gldb.schemas import PromptAttackHit
from gldb.usage import log_usage


@cache
def _default_client() -> Any:
    return boto3.client("bedrock-runtime", region_name=AWS_REGION)


def _hit(response: dict[str, Any]) -> str | None:
    """The confidence of a detected prompt attack, if any."""
    for assessment in response.get("assessments", []):
        for f in assessment.get("contentPolicy", {}).get("filters", []):
            if f.get("type") == "PROMPT_ATTACK" and f.get("detected", f.get("action") == "BLOCKED"):
                return f.get("confidence", "UNKNOWN")
    return None


def screen(
    pages: dict[str, list[str]], case_id: str, client: Any | None = None,
) -> dict[str, list[PromptAttackHit]]:
    """Screen every non-blank page; returns doc_id -> flagged pages."""
    guardrail = client or _default_client()
    hits: dict[str, list[PromptAttackHit]] = {}
    units = 0
    for doc_id, texts in pages.items():
        for page, text in enumerate(texts, start=1):
            if not text.strip():
                continue
            response = guardrail.apply_guardrail(
                guardrailIdentifier=GUARDRAIL_ID, guardrailVersion=GUARDRAIL_VERSION,
                source="INPUT", content=[{"text": {"text": text}}],
            )
            units += response.get("usage", {}).get("contentPolicyUnits", 0)
            confidence = _hit(response)
            if confidence is not None:
                hits.setdefault(doc_id, []).append(PromptAttackHit(page=page, confidence=confidence))
    if units:
        log_usage("guardrails", "prompt_screen", case_id, text_units=units)
    return hits
