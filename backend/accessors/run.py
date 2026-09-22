from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from backend.models.enums import RunStatus
from backend.models.run import RunCreate, RunRecord

logger = logging.getLogger(__name__)


class RunAccessor(Protocol):
    def create(self, run: RunCreate) -> RunRecord: ...
    def update_status(
        self,
        run_id: str | UUID,
        status: RunStatus,
        parsed_diff: list[dict[str, Any]] | None = None,
        error_message: str | None = None,
        raw_diff: str | None = None,
    ) -> RunRecord | None: ...


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

    def update_status(
        self,
        run_id: str | UUID,
        status: RunStatus,
        parsed_diff: list[dict[str, Any]] | None = None,
        error_message: str | None = None,
        raw_diff: str | None = None,
    ) -> RunRecord | None:
        for run in self.runs:
            if str(run.id) == str(run_id) or str(run.delivery_id) == str(run_id):
                run.status = status
                if error_message:
                    run.error_message = error_message
                if raw_diff:
                    run.raw_diff = raw_diff
                if status in {RunStatus.PUBLISHED, RunStatus.FAILED, RunStatus.SKIPPED}:
                    run.completed_at = datetime.now(UTC)
                return run

        # Auto-create if not present
        record = RunRecord(
            id=run_id if isinstance(run_id, UUID) else uuid4(),
            delivery_id=str(run_id),
            repository="",
            pr_number=1,
            head_sha="HEAD",
            base_sha="BASE",
            status=status,
            error_message=error_message,
            raw_diff=raw_diff,
            created_at=datetime.now(UTC),
            completed_at=(
                datetime.now(UTC)
                if status in {RunStatus.PUBLISHED, RunStatus.FAILED, RunStatus.SKIPPED}
                else None
            ),
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

    def update_status(
        self,
        run_id: str | UUID,
        status: RunStatus,
        parsed_diff: list[dict[str, Any]] | None = None,
        error_message: str | None = None,
        raw_diff: str | None = None,
    ) -> RunRecord | None:
        payload: dict[str, Any] = {"status": str(status)}
        if raw_diff is not None:
            payload["raw_diff"] = raw_diff
        if error_message is not None:
            payload["error_message"] = error_message
        if status in {RunStatus.PUBLISHED, RunStatus.FAILED, RunStatus.SKIPPED}:
            payload["completed_at"] = datetime.now(UTC).isoformat()

        try:
            response = self.client.table("runs").update(payload).eq("id", str(run_id)).execute()
            if response.data:
                return RunRecord.model_validate(response.data[0])
            # Fallback by delivery_id if passed
            response_by_delivery = self.client.table("runs").update(payload).eq("delivery_id", str(run_id)).execute()
            if response_by_delivery.data:
                return RunRecord.model_validate(response_by_delivery.data[0])
        except Exception as err:
            logger.warning(f"Failed to update run status in Supabase for {run_id}: {err}")
        return None

