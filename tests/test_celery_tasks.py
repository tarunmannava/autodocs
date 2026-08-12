import pytest
from unittest.mock import MagicMock, patch

def test_celery_app_configuration() -> None:
    """Test that Celery app is properly configured with Redis broker settings."""
    from backend.jobs.celery_app import celery_app

    assert celery_app.main == "autodocs"
    assert "redis" in celery_app.conf.broker_url


def test_process_pr_event_task_success() -> None:
    """Test that process_pr_event executes PR diff parsing and task pipeline."""
    from backend.jobs.tasks import process_pr_event

    job_payload = {
        "job_id": "test-job-123",
        "repository": "octo/example",
        "pr_number": 42,
        "head_sha": "head123",
        "base_sha": "base123",
    }

    result = process_pr_event.apply(args=[job_payload]).get()
    assert result["status"] == "success"
    assert result["job_id"] == "test-job-123"


def test_process_pr_event_task_retry_on_failure() -> None:
    """Test that process_pr_event task is configured with retries for transient errors."""
    from backend.jobs.tasks import process_pr_event

    assert process_pr_event.max_retries == 3
    assert process_pr_event.default_retry_delay == 5
