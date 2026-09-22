import subprocess
import sys
from pathlib import Path

from backend.accessors.run import InMemoryRunAccessor


def test_celery_app_configuration() -> None:
    """Test that Celery app is properly configured with Redis broker settings."""
    from backend.jobs.celery_app import celery_app

    assert celery_app.main == "autodocs"
    assert "redis" in celery_app.conf.broker_url
    if sys.platform == "win32":
        assert celery_app.conf.worker_pool == "solo"


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


def test_process_pr_event_with_local_clone(tmp_path: Path) -> None:
    """Test that process_pr_event accurately computes git diff on a local repository checkout."""
    from backend.jobs.tasks import process_pr_event

    # Setup a local git repo with 2 commits
    origin = tmp_path / "repo_origin"
    origin.mkdir()
    subprocess.run(["git", "init"], cwd=str(origin), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(origin), check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(origin), check=True)

    file_a = origin / "main.py"
    file_a.write_text("def hello(): pass\n")
    subprocess.run(["git", "add", "."], cwd=str(origin), check=True)
    subprocess.run(["git", "commit", "-m", "commit 1"], cwd=str(origin), check=True)

    file_a.write_text("def hello():\n    return 'world'\n")
    subprocess.run(["git", "commit", "-am", "commit 2"], cwd=str(origin), check=True)

    head_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(origin), capture_output=True, text=True, check=True)
    head_sha = head_res.stdout.strip()

    run_accessor = InMemoryRunAccessor()
    job_payload = {
        "job_id": "local-test-job-456",
        "repository": "octo/local",
        "pr_number": 1,
        "head_sha": head_sha,
        "clone_url": str(origin),
    }

    result = process_pr_event.apply(args=[job_payload], kwargs={"run_accessor": run_accessor}).get()
    assert result["status"] == "success"
    assert result["files_changed_count"] == 1
