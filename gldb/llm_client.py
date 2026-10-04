"""
gldb/llm_client.py
------------------
The only AWS Bedrock wrapper in the gldb-prototype.

Every LLM call goes through converse() (one call, temperature 0) or
converse_turn() (one turn of an agent's tool loop): Bedrock Converse API.
Each call is metered in the usage log (gldb.usage) against its case: token
counts only, never prompt or response text, so document contents cannot leak
into logs.
"""

from __future__ import annotations

import hashlib
from functools import cache
from typing import Any

import boto3
from botocore.config import Config
from pydantic import BaseModel

from gldb.config import AGENT_MAX_TOKENS, AWS_REGION, BEDROCK_MODEL_ID
from gldb.usage import log_usage

MAX_TOKENS: int = 4096
_TOOL_NAME = "emit"
#: The four specialists call Bedrock in parallel: back off and retry on
#: throttling (new accounts have low quotas) instead of failing the team
_RETRY = Config(retries={"mode": "adaptive", "max_attempts": 8})


class LLMResult(BaseModel):
    """Result of one Bedrock call. *data* is set only when a schema was given."""

    text: str
    data: dict[str, Any] | None = None
    input_tokens: int
    output_tokens: int


class Turn(BaseModel):
    """One model turn in an agent's tool loop. *content* is the assistant
    message's blocks as returned, ready to append to the conversation."""

    content: list[dict[str, Any]]
    stop_reason: str
    input_tokens: int
    output_tokens: int

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        """The model's tool calls: toolUseId, name and input of each."""
        return [b["toolUse"] for b in self.content if "toolUse" in b]

    @property
    def text(self) -> str:
        return "".join(b["text"] for b in self.content if "text" in b)


def prompt_version(*parts: str) -> str:
    """A short content hash of a prompt's parts (system prompt, output
    schema). Logged with each case so a result can be tied to the exact
    instructions the model was given; editing them changes the version
    without anyone having to remember to bump it."""
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:12]


@cache
def _default_client() -> Any:
    return boto3.client("bedrock-runtime", region_name=AWS_REGION, config=_RETRY)


def _send(request: dict[str, Any], *, purpose: str, case_id: str, client: Any | None) -> dict[str, Any]:
    """Send one Converse request and meter it against its case."""
    response: dict[str, Any] = (client or _default_client()).converse(**request)
    usage = response["usage"]
    log_usage("bedrock", purpose, case_id, model_id=request["modelId"],
              input_tokens=usage["inputTokens"], output_tokens=usage["outputTokens"])
    return response


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

    response = _send(request, purpose=purpose, case_id=case_id, client=client)

    blocks = response["output"]["message"]["content"]
    text = "".join(b["text"] for b in blocks if "text" in b)
    data = next((b["toolUse"]["input"] for b in blocks if "toolUse" in b), None)
    usage = response["usage"]
    return LLMResult(
        text=text,
        data=data,
        input_tokens=usage["inputTokens"],
        output_tokens=usage["outputTokens"],
    )


def tool_spec(name: str, description: str, schema: dict[str, Any], strict: bool = False) -> dict[str, Any]:
    """A Converse tool definition: *schema* is the JSON Schema of its input.
    *strict* makes the model's input always match it (strict tool use); only
    some models support it, and one that does not rejects the request."""
    spec: dict[str, Any] = {"name": name, "description": description, "inputSchema": {"json": schema}}
    if strict:
        spec["strict"] = True
    return {"toolSpec": spec}


def converse_turn(
    model_id: str,
    system: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]],
    purpose: str,
    case_id: str,
    client: Any | None = None,
) -> Turn:
    """Call Bedrock once for one turn of an agent loop.

    Sends no temperature and no toolChoice, so the same request is accepted by
    every model the agents may run on (Claude 4.6+, Haiku 4.5, Amazon Nova);
    a tool's strict flag is the caller's to set only for a model that supports it.

    Args:
        model_id: Bedrock model ID or inference profile of the agent.
        system: The agent's system prompt.
        messages: The conversation so far: user, assistant and tool-result turns.
        tools: Tool definitions (see tool_spec) the model may call.
        purpose: Usage-log label, "ai_team_<agent>".
        case_id: The case the call is spent on, for cost per case.
        client: Injectable bedrock-runtime client (tests). Defaults to boto3.
    """
    request: dict[str, Any] = {
        "modelId": model_id,
        "system": [{"text": system}],
        "messages": messages,
        "inferenceConfig": {"maxTokens": AGENT_MAX_TOKENS},
        "toolConfig": {"tools": tools},
    }
    response = _send(request, purpose=purpose, case_id=case_id, client=client)
    usage = response["usage"]
    return Turn(
        content=response["output"]["message"]["content"],
        stop_reason=response["stopReason"],
        input_tokens=usage["inputTokens"],
        output_tokens=usage["outputTokens"],
    )
