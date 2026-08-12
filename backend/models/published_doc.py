from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.models.enums import PublishedDocStatus


class PublishedDocCreate(BaseModel):
    run_id: UUID
    file_path: str
    diff: str | None = None
    status: PublishedDocStatus = PublishedDocStatus.GENERATED


class PublishedDocRecord(PublishedDocCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    commit_sha: str | None = None
    doc_pr_number: int | None = Field(default=None, gt=0)
    created_at: datetime
