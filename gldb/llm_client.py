"""
gldb/llm_client.py
------------------
The only AWS Bedrock wrapper in the gldb-prototype.

Every LLM call goes through converse(): Bedrock Converse API, temperature 0.
Each call is metered in the usage log (gldb.usage) against its case: token
counts only, never prompt or response text, so document contents cannot leak
into logs.
"""

from __future__ import annotations

import hashlib
from functools import cache
from typing import Any

import boto3
from pydantic import BaseModel

from gldb.config import AWS_REGION, BEDROCK_MODEL_ID
from gldb.usage import log_usage

MAX_TOKENS: int = 4096
_TOOL_NAME = "emit"


class LLMResult(BaseModel):
    """Result of one Bedrock call. *data* is set only when a schema was given."""

    text: str
    data: dict[str, Any] | None = None
    input_tokens: int
    output_tokens: int


def prompt_version(*parts: str) -> str:
    """A short content hash of a prompt's parts (system prompt, output
    schema). Logged with each case so a result can be tied to the exact
    instructions the model was given; editing them changes the version
    without anyone having to remember to bump it."""
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:12]


@cache
def _default_client() -> Any:
    return boto3.client("bedrock-runtime", region_name=AWS_REGION)


def converse(
    system: str,
    user: str,
    *,
    purpose: str,
    case_id: str = "",
    schema: dict[str, Any] | None = None,
    client: Any | None = None,
) -> LLMResult:
    """Call Bedrock once at temperature 0.

    Args:
        system: System prompt.
        user: User message (document text goes here, never in *system*).
        purpose: Short label for the usage log, e.g. "extract_invoice".
        case_id: The case the call is spent on, for cost per case.
        schema: Optional JSON Schema. When given, the model is forced to call
            a tool with this schema and the parsed arguments are returned in
            LLMResult.data.
        client: Injectable bedrock-runtime client (tests). Defaults to boto3.

    Raises:
        RuntimeError: BEDROCK_MODEL_ID is not configured.
    """
    if not BEDROCK_MODEL_ID:
        raise RuntimeError("BEDROCK_MODEL_ID is not set")

    request: dict[str, Any] = {
        "modelId": BEDROCK_MODEL_ID,
        "system": [{"text": system}],
        "messages": [{"role": "user", "content": [{"text": user}]}],
        "inferenceConfig": {"temperature": 0, "maxTokens": MAX_TOKENS},
    }
    if schema is not None:
        request["toolConfig"] = {
            "tools": [
                {
                    "toolSpec": {
                        "name": _TOOL_NAME,
                        "description": "Return the structured result.",
                        "inputSchema": {"json": schema},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": _TOOL_NAME}},
        }

    response = (client or _default_client()).converse(**request)

    blocks = response["output"]["message"]["content"]
    text = "".join(b["text"] for b in blocks if "text" in b)
    data = next((b["toolUse"]["input"] for b in blocks if "toolUse" in b), None)
    usage = response["usage"]
    result = LLMResult(
        text=text,
        data=data,
        input_tokens=usage["inputTokens"],
        output_tokens=usage["outputTokens"],
    )
    log_usage("bedrock", purpose, case_id, model_id=BEDROCK_MODEL_ID,
              input_tokens=result.input_tokens, output_tokens=result.output_tokens)
    return result
