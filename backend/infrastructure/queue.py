from dataclasses import asdict
from typing import Any, Protocol

from backend.models.jobs import PullRequestJob


class JobQueue(Protocol):
    def enqueue_pull_request(self, job: PullRequestJob) -> str: ...


class InlineJobQueue:
    """Development queue; replace with CeleryQueue in production."""

    def __init__(self) -> None:
        self.jobs: list[PullRequestJob] = []

    def enqueue_pull_request(self, job: PullRequestJob) -> str:
        self.jobs.append(job)
        return job.delivery_id


class CeleryQueue:
    """Production Celery background queue worker interface."""

    def __init__(self, celery_app: Any = None) -> None:
        if celery_app is None:
            from backend.jobs.celery_app import celery_app as default_app
            self.celery_app = default_app
        else:
            self.celery_app = celery_app

    def enqueue_pull_request(self, job: PullRequestJob) -> str:
        self.celery_app.send_task("backend.jobs.tasks.process_pr_event", kwargs={"job_payload": asdict(job)})
        return job.delivery_id
