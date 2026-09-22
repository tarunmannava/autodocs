import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from backend.accessors.run import RunAccessor
from backend.accessors.webhook_delivery import WebhookDeliveryAccessor
from backend.infrastructure.queue import JobQueue
from backend.models.jobs import PullRequestJob
from backend.models.run import RunCreate
from backend.models.webhook_delivery import WebhookDeliveryCreate


class InvalidWebhookSignatureError(ValueError):
    pass


class MalformedWebhookError(ValueError):
    pass


@dataclass(frozen=True)
class WebhookResult:
    status: str
    event: str
    delivery_id: str | None = None
    action: str | None = None
    reason: str | None = None
    job_id: str | None = None


def verify_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not signature.startswith("sha256=") or not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class WebhookService:
    def __init__(
        self,
        secret: str,
        queue: JobQueue,
        delivery_accessor: WebhookDeliveryAccessor,
        run_accessor: RunAccessor,
    ) -> None:
        self.secret = secret
        self.queue = queue
        self.delivery_accessor = delivery_accessor
        self.run_accessor = run_accessor

    def handle(
        self,
        body: bytes,
        event: str | None,
        delivery_id: str | None,
        signature: str | None,
        payload: dict[str, Any],
    ) -> WebhookResult:
        if not verify_signature(body, signature, self.secret):
            raise InvalidWebhookSignatureError
        if not delivery_id:
            raise MalformedWebhookError("Missing delivery ID")
        delivery = WebhookDeliveryCreate(
            delivery_id=delivery_id,
            event_type=event or "unknown",
            action=payload.get("action"),
            repository=self._repository_name(payload),
        )
        if not self.delivery_accessor.claim(delivery):
            return WebhookResult(
                status="ignored", event=event or "unknown", delivery_id=delivery_id, reason="duplicate"
            )

        if event not in {"pull_request", "ping"}:
            return WebhookResult(status="ignored", event=event or "unknown", delivery_id=delivery_id)
        if event == "ping":
            return WebhookResult(status="ok", event="ping", delivery_id=delivery_id)

        action = payload.get("action")
        if action != "closed":
            return WebhookResult(
                status="ignored",
                event="pull_request",
                delivery_id=delivery_id,
                action=str(action),
                reason="not_a_merge_event",
            )
        pull_request = payload.get("pull_request")
        if not isinstance(pull_request, dict) or pull_request.get("merged") is not True:
            return WebhookResult(
                status="ignored",
                event="pull_request",
                delivery_id=delivery_id,
                action="closed",
                reason="pull_request_not_merged",
            )

        try:
            initial_job = self._build_pull_request_job(payload, delivery_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedWebhookError from exc

        run_record = self.run_accessor.create(
            RunCreate(
                repository=initial_job.repository,
                pr_number=initial_job.pr_number,
                delivery_id=initial_job.delivery_id,
                head_sha=initial_job.head_sha,
                base_sha=initial_job.base_sha,
            )
        )
        run_id_str = str(run_record.id)
        job = PullRequestJob(
            delivery_id=delivery_id,
            repository=initial_job.repository,
            pr_number=initial_job.pr_number,
            head_sha=initial_job.head_sha,
            base_sha=initial_job.base_sha,
            job_id=run_id_str,
            clone_url=initial_job.clone_url,
            diff_text=initial_job.diff_text,
        )
        self.queue.enqueue_pull_request(job)
        return WebhookResult(
            status="queued", event="pull_request", delivery_id=delivery_id, action="closed", job_id=delivery_id
        )

    @staticmethod
    def _repository_name(payload: dict[str, Any]) -> str:
        repository = payload.get("repository", {})
        if isinstance(repository, dict):
            return str(repository.get("full_name", "unknown"))
        return "unknown"

    @staticmethod
    def _build_pull_request_job(payload: dict[str, Any], delivery_id: str) -> PullRequestJob:
        pull_request = payload["pull_request"]
        repository = payload.get("repository", {})
        repo_name = repository.get("full_name", "unknown") if isinstance(repository, dict) else str(repository)
        clone_url = repository.get("clone_url") if isinstance(repository, dict) else None
        pr_title = str(pull_request.get("title", "")) if isinstance(pull_request, dict) else ""
        return PullRequestJob(
            delivery_id=delivery_id,
            repository=repo_name,
            pr_number=pull_request["number"],
            head_sha=pull_request["head"]["sha"],
            base_sha=pull_request["base"]["sha"],
            clone_url=clone_url,
            pr_title=pr_title,
        )
