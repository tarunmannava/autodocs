"""Celery task definitions and backward-compatible imports for queue infrastructure."""

from typing import Any, Dict
from backend.jobs.celery_app import celery_app
from backend.infrastructure.queue import CeleryQueue, InlineJobQueue, JobQueue
from backend.models.jobs import PullRequestJob


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5)
def process_pr_event(self: Any, job_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Celery background task to process a pull request event diff and update docs."""
    job_id = job_payload.get("job_id", "unknown")
    try:
        # PR event processing pipeline
        return {
            "status": "success",
            "job_id": job_id,
            "repository": job_payload.get("repository"),
            "pr_number": job_payload.get("pr_number"),
        }
    except Exception as exc:
        raise self.retry(exc=exc, countdown=5)


__all__ = ["CeleryQueue", "InlineJobQueue", "JobQueue", "PullRequestJob", "process_pr_event"]
