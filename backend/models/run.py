from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.models.enums import RunStatus


class RunCreate(BaseModel):
    repository: str
    pr_number: int = Field(gt=0)
    delivery_id: str
    head_sha: str
    base_sha: str


class RunRecord(RunCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: RunStatus
    raw_diff: str | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
