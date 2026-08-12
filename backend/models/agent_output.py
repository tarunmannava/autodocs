from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.models.enums import AgentStatus


class AgentOutputCreate(BaseModel):
    run_id: UUID
    agent_name: str
    status: AgentStatus = AgentStatus.QUEUED
    structured_output: dict | None = None


class AgentOutputRecord(AgentOutputCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
