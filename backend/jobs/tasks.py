import logging
import subprocess
from typing import Any, Dict

from backend.accessors.run import SupabaseRunAccessor
from backend.infrastructure.queue import CeleryQueue, InlineJobQueue, JobQueue
from backend.infrastructure.supabase import get_supabase_client
from backend.jobs.celery_app import celery_app
from backend.models.enums import RunStatus
from backend.models.jobs import PullRequestJob
from repo_manager.checkout import temporary_repository_checkout
from repo_manager.diff import parse_unified_diff

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5)
def process_pr_event(self: Any, job_payload: Dict[str, Any], run_accessor: Any | None = None) -> Dict[str, Any]:
    """Celery background task to process a pull request event diff and update docs."""
    job_id = job_payload.get("job_id") or job_payload.get("delivery_id") or "unknown"
    repository = job_payload.get("repository", "")
    pr_number = job_payload.get("pr_number", 0)
    commit_sha = job_payload.get("commit_sha") or job_payload.get("head_sha") or "HEAD"
    base_sha = job_payload.get("base_sha")
    clone_url = job_payload.get("clone_url")
    diff_text = job_payload.get("diff_text")

    if run_accessor is None:
        supabase_client = get_supabase_client()
        run_accessor = SupabaseRunAccessor(client=supabase_client) if supabase_client else None

    try:
        parsed_files = []

        if diff_text:
            parsed = parse_unified_diff(diff_text, filter_ignored=True)
            parsed_files = [f.to_dict() for f in parsed]
            if run_accessor:
                run_accessor.update_status(
                    run_id=job_id, status=RunStatus.DIFFING, parsed_diff=parsed_files, raw_diff=diff_text
                )
        elif clone_url:
            if run_accessor:
                run_accessor.update_status(run_id=job_id, status=RunStatus.CLONING)

            with temporary_repository_checkout(
                clone_url=clone_url,
                commit_sha=commit_sha,
                base_sha=base_sha,
            ) as repo_dir:
                if run_accessor:
                    run_accessor.update_status(run_id=job_id, status=RunStatus.DIFFING)

                # Attempt diff against base_sha first if available, then HEAD~1 HEAD, then root commit
                raw_diff = ""
                if base_sha and base_sha != commit_sha:
                    diff_res = subprocess.run(
                        ["git", "diff", f"{base_sha}...{commit_sha}"],
                        cwd=str(repo_dir),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if diff_res.returncode == 0 and diff_res.stdout.strip():
                        raw_diff = diff_res.stdout

                if not raw_diff:
                    diff_res = subprocess.run(
                        ["git", "diff", "HEAD~1", "HEAD"],
                        cwd=str(repo_dir),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if diff_res.returncode == 0:
                        raw_diff = diff_res.stdout
                    else:
                        # Fallback for initial commit without parent
                        show_res = subprocess.run(
                            ["git", "show", "HEAD", "--format="],
                            cwd=str(repo_dir),
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if show_res.returncode == 0:
                            raw_diff = show_res.stdout

                if raw_diff:
                    parsed = parse_unified_diff(raw_diff, filter_ignored=True)
                    parsed_files = [f.to_dict() for f in parsed]

                if run_accessor:
                    run_accessor.update_status(
                        run_id=job_id, status=RunStatus.PLANNING, parsed_diff=parsed_files, raw_diff=raw_diff
                    )
        else:
            if run_accessor:
                run_accessor.update_status(run_id=job_id, status=RunStatus.DIFFING, parsed_diff=[])

        return {
            "status": "success",
            "job_id": job_id,
            "repository": repository,
            "pr_number": pr_number,
            "files_changed_count": len(parsed_files),
        }
    except Exception as exc:
        logger.error(f"Error executing process_pr_event job {job_id}: {exc}")
        if run_accessor:
            run_accessor.update_status(run_id=job_id, status=RunStatus.FAILED, error_message=str(exc))
        raise self.retry(exc=exc, countdown=5)




__all__ = ["CeleryQueue", "InlineJobQueue", "JobQueue", "PullRequestJob", "process_pr_event"]

