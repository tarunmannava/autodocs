from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from agents.doc_agent import DocumentationAgent
from agents.llm import get_chat_model
from backend.accessors.run import SupabaseRunAccessor
from backend.config import get_settings
from backend.infrastructure.queue import CeleryQueue, InlineJobQueue, JobQueue
from backend.infrastructure.supabase import get_supabase_client
from backend.jobs.celery_app import celery_app
from backend.models.enums import RunStatus
from backend.models.jobs import PullRequestJob
from backend.services.publisher import (
    create_docs_branch_and_commit,
    extract_repo_name,
    format_source_pr_comment,
    open_documentation_pr,
    post_source_pr_comment,
    push_docs_branch,
)
from mcp_server.code_intel_server import create_code_intel_server
from mcp_server.docs_workspace_server import create_docs_workspace_server
from repo_manager.checkout import temporary_repository_checkout
from repo_manager.diff import parse_unified_diff

logger = logging.getLogger(__name__)


@contextmanager
def get_or_checkout_repo(
    clone_url: Optional[str] = None,
    local_dir: Optional[Path] = None,
    branch: Optional[str] = None,
    commit_sha: Optional[str] = None,
    base_sha: Optional[str] = None,
    token: Optional[str] = None,
) -> Generator[Path, None, None]:
    """
    Context manager that yields a Path to a repository workspace, either using
    an existing local directory or performing a temporary clone and cleanup.
    """
    if local_dir is not None and local_dir.exists():
        yield local_dir
    elif clone_url:
        with temporary_repository_checkout(
            clone_url=clone_url,
            branch=branch,
            commit_sha=commit_sha,
            base_sha=base_sha,
            token=token,
        ) as repo_path:
            yield repo_path
    else:
        temp_dir = tempfile.mkdtemp(prefix="autodocs_workspace_")
        p = Path(temp_dir)
        try:
            yield p
        finally:
            shutil.rmtree(p, ignore_errors=True)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5)
def process_pr_event(
    self: Any,
    job_payload: Dict[str, Any],
    run_accessor: Any | None = None,
    agent: Any | None = None,
    agent_model: Any | None = None,
) -> Dict[str, Any]:
    """
    Celery background task to process a pull request event diff and synchronize documentation.
    Orchestration sequence:
      1. Source repo checkout / git diff extraction.
      2. Target docs repo checkout.
      3. FastMCP servers instantiation (Code Intel + Docs Workspace).
      4. DocumentationAgent execution with OpenRouter.
      5. Deterministic guardrails check (Markdown syntax & link integrity).
      6. Staging, committing branch, opening PR on docs repo, and commenting on source PR.
    """
    settings = get_settings()
    job_id = job_payload.get("job_id") or job_payload.get("delivery_id") or "unknown"
    repository = job_payload.get("repository", "")
    pr_number = job_payload.get("pr_number", 0)
    pr_title = job_payload.get("pr_title") or f"PR #{pr_number}"
    commit_sha = job_payload.get("commit_sha") or job_payload.get("head_sha") or "HEAD"
    base_sha = job_payload.get("base_sha")
    clone_url = job_payload.get("clone_url")
    diff_text = job_payload.get("diff_text")
    source_dir_path = job_payload.get("source_dir")
    docs_dir_path = job_payload.get("docs_dir")
    docs_repo_url = job_payload.get("docs_repo_url") or settings.docs_repo_url
    docs_repo_branch = job_payload.get("docs_repo_branch") or settings.docs_repo_branch or "main"

    if run_accessor is None:
        supabase_client = get_supabase_client()
        run_accessor = SupabaseRunAccessor(client=supabase_client) if supabase_client else None

    try:
        parsed_files: List[Dict[str, Any]] = []
        raw_diff = diff_text or ""

        # Early exit if PR is an automated AutoDocs branch
        head_branch = job_payload.get("head_branch") or job_payload.get("branch") or ""
        if head_branch.startswith("docs/sync-") or pr_title.startswith("docs: sync docs for"):
            logger.info(f"Skipping AutoDocs documentation PR #{pr_number} ({pr_title})")
            if run_accessor:
                run_accessor.update_status(run_id=job_id, status=RunStatus.SKIPPED)
            return {
                "status": "skipped",
                "job_id": job_id,
                "repository": repository,
                "pr_number": pr_number,
                "reason": "automated_documentation_pr",
                "modified_files": [],
                "files_changed_count": 0,
            }

        # Early exit if neither diff nor clone_url nor source_dir provided
        if not diff_text and not clone_url and not source_dir_path:
            if run_accessor:
                run_accessor.update_status(run_id=job_id, status=RunStatus.DIFFING, parsed_diff=[])
            return {
                "status": "success",
                "job_id": job_id,
                "repository": repository,
                "pr_number": pr_number,
                "files_changed_count": 0,
            }

        # Step 1: Source repo checkout & diff computation
        if clone_url:
            if run_accessor:
                run_accessor.update_status(run_id=job_id, status=RunStatus.CLONING)

        with get_or_checkout_repo(
            clone_url=clone_url,
            local_dir=Path(source_dir_path) if source_dir_path else None,
            commit_sha=commit_sha,
            base_sha=base_sha,
        ) as source_dir:
            if run_accessor:
                run_accessor.update_status(run_id=job_id, status=RunStatus.DIFFING)

            if not raw_diff and (clone_url or source_dir_path):
                # Diff against base_sha first if available, then HEAD~1 HEAD, then root commit
                if base_sha and base_sha != commit_sha:
                    diff_res = subprocess.run(
                        ["git", "diff", f"{base_sha}...{commit_sha}"],
                        cwd=str(source_dir),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if diff_res.returncode == 0 and diff_res.stdout.strip():
                        raw_diff = diff_res.stdout
                    else:
                        # Fallback to direct two-tree diff if merge-base is missing in shallow history
                        fallback_diff = subprocess.run(
                            ["git", "diff", base_sha, commit_sha],
                            cwd=str(source_dir),
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if fallback_diff.returncode == 0 and fallback_diff.stdout.strip():
                            raw_diff = fallback_diff.stdout

                if not raw_diff:
                    diff_res = subprocess.run(
                        ["git", "diff", "HEAD~1", "HEAD"],
                        cwd=str(source_dir),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if diff_res.returncode == 0:
                        raw_diff = diff_res.stdout
                    else:
                        show_res = subprocess.run(
                            ["git", "show", "HEAD", "--format="],
                            cwd=str(source_dir),
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if show_res.returncode == 0:
                            raw_diff = show_res.stdout

            if raw_diff:
                parsed = parse_unified_diff(raw_diff, filter_ignored=True)
                parsed_files = [f.to_dict() for f in parsed]

            # Early exit if only documentation files were modified in the PR
            if (clone_url or source_dir_path) and parsed_files:
                doc_extensions = {".md", ".mdx", ".rst", ".txt"}
                only_docs = all(
                    any((f.get("path") or f.get("new_path") or "").lower().endswith(ext) for ext in doc_extensions)
                    or (f.get("path") or f.get("new_path") or "").lower().startswith("docs/")
                    for f in parsed_files
                )
                if only_docs:
                    logger.info(
                        f"AutoDocs run {job_id}: PR #{pr_number} modifies only documentation files. Skipping agent execution."
                    )
                    if run_accessor:
                        run_accessor.update_status(
                            run_id=job_id, status=RunStatus.SKIPPED, parsed_diff=parsed_files, raw_diff=raw_diff
                        )
                    return {
                        "status": "skipped",
                        "job_id": job_id,
                        "repository": repository,
                        "pr_number": pr_number,
                        "reason": "only_documentation_files_changed",
                        "modified_files": [],
                        "files_changed_count": len(parsed_files),
                    }

            if run_accessor:
                run_accessor.update_status(
                    run_id=job_id, status=RunStatus.PLANNING, parsed_diff=parsed_files, raw_diff=raw_diff
                )

            # Step 2: Target documentation workspace setup
            effective_local_docs_dir: Optional[Path] = None
            if docs_dir_path:
                effective_local_docs_dir = Path(docs_dir_path)
            elif not docs_repo_url:
                if (source_dir / "docs").exists() or (source_dir / "README.md").exists():
                    effective_local_docs_dir = source_dir

            should_run_agent = bool(agent or agent_model or docs_repo_url or docs_dir_path)

            if not should_run_agent and not effective_local_docs_dir:
                # No documentation workspace configured
                return {
                    "status": "success",
                    "job_id": job_id,
                    "repository": repository,
                    "pr_number": pr_number,
                    "files_changed_count": len(parsed_files),
                }

            with get_or_checkout_repo(
                clone_url=docs_repo_url,
                local_dir=effective_local_docs_dir,
                branch=docs_repo_branch,
                token=settings.github_token,
            ) as docs_dir:
                # Step 3: FastMCP servers and DocumentationAgent execution
                if run_accessor:
                    run_accessor.update_status(run_id=job_id, status=RunStatus.RUNNING_AGENTS)

                code_server = create_code_intel_server(source_dir, raw_diff=raw_diff)
                google_doc_id = (
                    settings.google_docs_document_id if settings.google_docs_enabled else None
                )
                docs_server = create_docs_workspace_server(
                    docs_dir=docs_dir,
                    google_docs_id=google_doc_id,
                    google_credentials_path=settings.google_credentials_path,
                )

                agent_runner = agent
                if agent_runner is None:
                    model = agent_model or get_chat_model(settings=settings)
                    agent_runner = DocumentationAgent(model=model)

                agent_result = agent_runner.run(
                    code_intel_server=code_server,
                    docs_workspace_server=docs_server,
                    docs_dir=docs_dir,
                    repo_name=repository,
                    pr_number=pr_number,
                    pr_title=pr_title,
                )

                if not agent_result.success:
                    err_msg = "Agent execution failed"
                    if agent_result.guardrail_result and not agent_result.guardrail_result.passed:
                        err_msg = f"Guardrail violations: {'; '.join(agent_result.guardrail_result.errors)}"
                    logger.error(f"AutoDocs run {job_id} failed: {err_msg}")
                    if run_accessor:
                        run_accessor.update_status(run_id=job_id, status=RunStatus.FAILED, error_message=err_msg)
                    return {
                        "status": "failed",
                        "job_id": job_id,
                        "repository": repository,
                        "pr_number": pr_number,
                        "error": err_msg,
                        "guardrails": (
                            agent_result.guardrail_result.to_dict()
                            if agent_result.guardrail_result
                            else None
                        ),
                        "files_changed_count": len(parsed_files),
                    }

                if not agent_result.modified_files:
                    logger.info(f"AutoDocs run {job_id}: no documentation changes needed.")
                    if run_accessor:
                        run_accessor.update_status(run_id=job_id, status=RunStatus.SKIPPED)
                    return {
                        "status": "skipped",
                        "job_id": job_id,
                        "repository": repository,
                        "pr_number": pr_number,
                        "reason": "no_documentation_changes_needed",
                        "modified_files": [],
                        "files_changed_count": len(parsed_files),
                    }

                # Step 4: Staging & Multi-Repo Publishing
                if run_accessor:
                    run_accessor.update_status(run_id=job_id, status=RunStatus.BUILDING)

                repo_slug = repository.replace("/", "-") if repository else "repo"
                branch_name = f"docs/sync-{repo_slug}-pr-{pr_number}"
                commit_msg = f"docs: synchronize documentation for {repository}#{pr_number}\n\n{agent_result.summary}"

                commit_sha = ""
                try:
                    commit_sha = create_docs_branch_and_commit(
                        docs_dir=docs_dir,
                        branch_name=branch_name,
                        commit_message=commit_msg,
                    )
                except Exception as pub_err:
                    logger.warning(f"Could not commit docs changes in {docs_dir}: {pub_err}")

                docs_pr_url: Optional[str] = None
                if settings.github_token:
                    push_docs_branch(docs_dir=docs_dir, branch_name=branch_name)
                    docs_repo_name = extract_repo_name(docs_repo_url) if docs_repo_url else repository
                    pr_desc = (
                        "## 📚 AutoDocs Documentation Synchronization\n\n"
                        f"Synchronized documentation updates for changes in `{repository}#{pr_number}`\n"
                        f"({pr_title}).\n\n"
                        f"### Agent Summary\n{agent_result.summary}"
                    )
                    docs_pr_url = open_documentation_pr(
                        docs_repo=docs_repo_name,
                        branch_name=branch_name,
                        base_branch=docs_repo_branch,
                        title=f"docs: sync docs for {repository}#{pr_number} ({pr_title})",
                        body=pr_desc,
                        github_token=settings.github_token,
                    )
                    comment_text = format_source_pr_comment(
                        modified_files=agent_result.modified_files,
                        docs_pr_url=docs_pr_url,
                        branch_name=branch_name,
                        summary=agent_result.summary,
                        run_id=job_id,
                    )
                    post_source_pr_comment(
                        source_repo=repository,
                        pr_number=pr_number,
                        comment_body=comment_text,
                        github_token=settings.github_token,
                    )

                google_doc_url: Optional[str] = None
                if settings.google_docs_enabled and settings.google_docs_document_id:
                    try:
                        from backend.services.google_docs import GoogleDocsService
                        target_file = Path(docs_dir) / (
                            agent_result.modified_files[0] if agent_result.modified_files else "README.md"
                        )
                        gdocs = GoogleDocsService(credentials_path=settings.google_credentials_path)
                        if target_file.exists():
                            google_doc_url = gdocs.sync_markdown_to_doc(
                                document_id=settings.google_docs_document_id,
                                markdown_text=target_file.read_text(encoding="utf-8"),
                                clear_first=False,
                            )
                            logger.info(f"Updated Google Doc: {google_doc_url}")
                    except Exception as gdoc_err:
                        logger.warning(f"Google Docs sync failed: {gdoc_err}")

                if run_accessor:
                    run_accessor.update_status(run_id=job_id, status=RunStatus.PUBLISHED)

                return {
                    "status": "success",
                    "job_id": job_id,
                    "repository": repository,
                    "pr_number": pr_number,
                    "files_changed_count": len(parsed_files),
                    "modified_files": agent_result.modified_files,
                    "commit_sha": commit_sha,
                    "branch_name": branch_name,
                    "docs_pr_url": docs_pr_url,
                    "google_doc_url": google_doc_url,
                }
    except Exception as exc:
        logger.error(f"Error executing process_pr_event job {job_id}: {exc}")
        if run_accessor:
            run_accessor.update_status(run_id=job_id, status=RunStatus.FAILED, error_message=str(exc))
        raise self.retry(exc=exc, countdown=5)


__all__ = ["CeleryQueue", "InlineJobQueue", "JobQueue", "PullRequestJob", "process_pr_event"]
