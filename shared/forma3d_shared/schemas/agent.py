"""Agent job contracts (backend <-> agent worker) and the structured LLM response schema."""

from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from forma3d_shared.schemas.scene_object import SceneInventory

AGENT_JOB_STREAM = "jobs:agent"
AGENT_JOB_GROUP = "agent-workers"

Intent = Literal["modeling", "question", "clarify", "export"]


class AgentJobSpec(BaseModel):
    """Queue payload. Deliberately minimal: the worker loads context through the backend so it
    always sees the committed head at execution time, never a stale snapshot."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    project_id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AgentContext(BaseModel):
    job_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    project_description: str = ""
    version_number: Optional[int] = None
    inventory: SceneInventory
    history: list[ChatTurn] = Field(default_factory=list)
    user_message: str
    selected_object_id: Optional[str] = None


class LLMResponse(BaseModel):
    """What the planner model must return (loosely typed operations; the DSL validator is the
    authority). Kept small so guided decoding stays fast."""

    model_config = ConfigDict(extra="ignore")

    intent: Intent
    reply: str = Field(max_length=2000)
    summary: str = Field(default="", max_length=500)
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


#: JSON schema used for guided decoding (vLLM `guided_json` / OpenAI `json_schema`).
def llm_response_json_schema(operation_types: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "reply", "summary", "operations"],
        "properties": {
            "intent": {"type": "string", "enum": ["modeling", "question", "clarify", "export"]},
            "reply": {"type": "string", "maxLength": 2000},
            "summary": {"type": "string", "maxLength": 500},
            "operations": {
                "type": "array",
                "maxItems": 200,
                "items": {
                    "type": "object",
                    "required": ["operation_id", "operation_type", "parameters"],
                    "properties": {
                        "operation_id": {"type": "string", "maxLength": 64},
                        "operation_type": {"type": "string", "enum": operation_types},
                        "target_object_id": {"type": "string", "maxLength": 64},
                        "dependencies": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
                        "parameters": {"type": "object"},
                        "metadata": {"type": "object"},
                    },
                },
            },
        },
    }


class SubmitPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=500)
    operations: list[dict[str, Any]] = Field(max_length=2000)
    plan: dict[str, Any] = Field(default_factory=dict)


class SubmitPlanResponse(BaseModel):
    modeling_job_id: uuid.UUID
    operation_batch_id: uuid.UUID


class AgentRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(max_length=64)
    model_name: Optional[str] = Field(default=None, max_length=128)
    status: Literal["succeeded", "failed"]
    duration_ms: int = 0
    input_metadata: dict[str, Any] = Field(default_factory=dict)
    output_metadata: dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = Field(default=None, max_length=2000)


class AgentCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    intent: Optional[Intent] = None
    reply: str = Field(default="", max_length=4000)
    modeling_job_id: Optional[uuid.UUID] = None
    error_code: Optional[str] = Field(default=None, max_length=64)
    error_message: Optional[str] = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)
