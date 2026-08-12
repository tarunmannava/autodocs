from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

if TYPE_CHECKING:
    from supabase import Client

from backend.models.enums import RunStatus
from backend.models.run import RunCreate, RunRecord


class RunAccessor(Protocol):
    def create(self, run: RunCreate) -> RunRecord: ...


class InMemoryRunAccessor:
    def __init__(self) -> None:
        self.runs: list[RunRecord] = []

    def create(self, run: RunCreate) -> RunRecord:
        record = RunRecord(
            **run.model_dump(),
            id=uuid4(),
            status=RunStatus.QUEUED,
            created_at=datetime.now(UTC),
        )
        self.runs.append(record)
        return record


class SupabaseRunAccessor:
    def __init__(self, client: Any) -> None:
        self.client = client

    def create(self, run: RunCreate) -> RunRecord:
        response = self.client.table("runs").insert(run.model_dump(mode="json")).execute()
        if not response.data:
            raise RuntimeError("Supabase did not return the created run")
        return RunRecord.model_validate(response.data[0])
