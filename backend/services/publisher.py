from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


def create_docs_branch_and_commit(
    docs_dir: Path,
    branch_name: str,
    commit_message: str,
    author_name: str = "AutoDocs Bot",
    author_email: str = "bot@autodocs.dev",
) -> str:
    """
    Creates or resets a Git branch in the documentation directory, stages all changes,
    and creates a commit with author attribution.

    Args:
        docs_dir: Absolute path to the checked-out documentation repository.
        branch_name: Name of the Git branch to create/switch to (e.g. 'docs/update-repo-pr-42').
        commit_message: Commit message describing the changes.
        author_name: Commit author name.
        author_email: Commit author email.

    Returns:
        str: The newly created commit SHA, or empty string "" if there were no changes to commit.

    Raises:
        RuntimeError: If Git commands fail unexpectedly.
    """
    cwd = str(docs_dir)

    # Verify directory is a git repo
    chk = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=cwd, capture_output=True, text=True)
    if chk.returncode != 0:
        raise RuntimeError(f"Directory {cwd} is not a valid git repository: {chk.stderr.strip()}")

    # Switch to / create branch
    checkout_res = subprocess.run(["git", "checkout", "-B", branch_name], cwd=cwd, capture_output=True, text=True)
    if checkout_res.returncode != 0:
        raise RuntimeError(f"Failed to create/checkout branch {branch_name}: {checkout_res.stderr.strip()}")

    # Stage all changes
    add_res = subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, text=True)
    if add_res.returncode != 0:
        raise RuntimeError(f"Failed to stage changes in {cwd}: {add_res.stderr.strip()}")

    # Check if there are staged changes
    status_res = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True)
    if not status_res.stdout.strip():
        logger.info(f"No changes staged in {cwd}; skipping commit.")
        return ""

    # Commit changes
    author_flag = f"--author={author_name} <{author_email}>"
    commit_res = subprocess.run(
        ["git", "commit", "-m", commit_message, author_flag],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if commit_res.returncode != 0:
        raise RuntimeError(f"Failed to commit changes in {cwd}: {commit_res.stderr.strip()}")

    # Retrieve HEAD commit SHA
    sha_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True)
    if sha_res.returncode != 0:
        raise RuntimeError(f"Failed to get HEAD commit SHA in {cwd}: {sha_res.stderr.strip()}")

    commit_sha = sha_res.stdout.strip()
    logger.info(f"Committed documentation changes in {branch_name}: {commit_sha}")
    return commit_sha


def push_docs_branch(
    docs_dir: Path,
    branch_name: str,
    remote: str = "origin",
    force: bool = False,
) -> bool:
    """
    Pushes the documentation branch to the remote repository.

    Args:
        docs_dir: Absolute path to the checked-out documentation repository.
        branch_name: Branch name to push.
        remote: Remote name (default 'origin').
        force: Whether to force push (default False).

    Returns:
        bool: True if push succeeded, False otherwise.
    """
    cwd = str(docs_dir)
    cmd = ["git", "push", "-u", remote, branch_name]
    if force:
        cmd.append("--force")

    push_res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if push_res.returncode != 0:
        logger.warning(f"Failed to push {branch_name} to {remote}: {push_res.stderr.strip()}")
        return False

    logger.info(f"Successfully pushed branch {branch_name} to {remote}")
    return True


def open_documentation_pr(
    docs_repo: str,
    branch_name: str,
    base_branch: str = "main",
    title: str = "",
    body: str = "",
    github_token: str = "",
    client: Optional[httpx.Client] = None,
    api_base_url: str = "https://api.github.com",
    pr_title: Optional[str] = None,
    pr_body: Optional[str] = None,
) -> Optional[str]:
    """
    Opens a Pull Request in the target documentation repository via GitHub REST API.

    Args:
        docs_repo: Full name of the docs repo (e.g. 'org/developer-docs').
        branch_name: Head branch containing the docs changes.
        base_branch: Base branch to merge into (default 'main').
        title: PR title.
        body: PR description.
        github_token: GitHub personal access token or App token.
        client: Optional httpx.Client instance for connection reuse or testing.
        api_base_url: Base URL for GitHub API (default 'https://api.github.com').

    Returns:
        Optional[str]: The html_url of the created or existing pull request, or None on failure.
    """
    if not github_token or not docs_repo:
        logger.info("GitHub token or docs_repo not provided; skipping PR creation.")
        return None

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    effective_title = pr_title if pr_title is not None else title
    effective_body = pr_body if pr_body is not None else body

    payload: Dict[str, Any] = {
        "title": effective_title or f"docs: update documentation for {branch_name}",
        "head": branch_name,
        "base": base_branch,
        "body": effective_body,
    }

    url = f"{api_base_url.rstrip('/')}/repos/{docs_repo}/pulls"

    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0)
        close_client = True

    try:
        response = client.post(url, headers=headers, json=payload)
        if response.status_code == 201:
            data = response.json()
            pr_url: str = data.get("html_url", "")
            logger.info(f"Created documentation PR: {pr_url}")
            return pr_url

        # Check if PR already exists (422 Unprocessable Entity)
        if response.status_code == 422:
            logger.info(f"PR for {branch_name} might already exist, checking open PRs...")
            list_resp = client.get(
                f"{url}?head={docs_repo.split('/')[0]}:{branch_name}&state=open",
                headers=headers,
            )
            if list_resp.status_code == 200:
                prs = list_resp.json()
                if isinstance(prs, list) and len(prs) > 0:
                    existing_url: str = prs[0].get("html_url", "")
                    logger.info(f"Found existing open PR: {existing_url}")
                    return existing_url

        logger.warning(
            f"Failed to create documentation PR in {docs_repo}. Status: {response.status_code}, Body: {response.text}"
        )
        return None
    except Exception as exc:
        logger.error(f"Exception creating documentation PR in {docs_repo}: {exc}")
        return None
    finally:
        if close_client:
            client.close()


def post_source_pr_comment(
    source_repo: str = "",
    pr_number: int = 0,
    comment_body: str = "",
    github_token: str = "",
    client: Optional[httpx.Client] = None,
    api_base_url: str = "https://api.github.com",
    repo_name: Optional[str] = None,
) -> bool:
    effective_repo = repo_name or source_repo
    """
    Posts an informative feedback comment on the source repository's Pull Request.

    Args:
        source_repo: Full name of source repo (e.g. 'org/payments-service').
        pr_number: Pull request number in source repo.
        comment_body: Markdown content for the comment.
        github_token: GitHub token with pull request comment permissions.
        client: Optional httpx.Client instance.
        api_base_url: Base URL for GitHub API.

    Returns:
        bool: True if comment was successfully posted, False otherwise.
    """
    if not github_token or not effective_repo or pr_number <= 0:
        logger.info("GitHub token or source repo/pr_number missing; skipping PR comment.")
        return False

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{api_base_url.rstrip('/')}/repos/{effective_repo}/issues/{pr_number}/comments"

    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0)
        close_client = True

    try:
        response = client.post(url, headers=headers, json={"body": comment_body})
        if response.status_code == 201:
            logger.info(f"Successfully posted comment on {effective_repo}#{pr_number}")
            return True

        logger.warning(
            f"Failed to post comment on {effective_repo}#{pr_number}. "
            f"Status: {response.status_code}, Body: {response.text}"
        )
        return False
    except Exception as exc:
        logger.error(f"Exception posting comment on {effective_repo}#{pr_number}: {exc}")
        return False
    finally:
        if close_client:
            client.close()


def format_source_pr_comment(
    modified_files: Optional[List[str]] = None,
    docs_pr_url: Optional[str] = None,
    branch_name: Optional[str] = None,
    summary: str = "",
    run_id: Optional[str] = None,
    agent_summary: Optional[str] = None,
    docs_repo: Optional[str] = None,
) -> str:
    effective_summary = agent_summary or summary
    effective_files = modified_files or []
    """
    Builds a formatted Markdown comment summarizing documentation synchronizations.

    Args:
        modified_files: List of relative paths of updated docs files.
        docs_pr_url: URL to the newly created documentation Pull Request (if opened).
        branch_name: Branch name in docs repo (if PR not opened).
        summary: Agent summary of the changes made.
        run_id: Optional AutoDocs run identifier for traceability.

    Returns:
        str: GitHub flavored markdown comment text.
    """
    lines: List[str] = [
        "### 📚 AutoDocs: Documentation Synchronization",
        "",
        "AutoDocs detected code and signature changes in this PR and generated documentation updates.",
        "",
    ]

    if effective_files:
        lines.extend([
            "| Documentation File | Status |",
            "| :--- | :--- |",
        ])
        for f in effective_files:
            lines.append(f"| `{f}` | ✏️ Updated |")
        lines.append("")

    if docs_pr_url:
        lines.append(f"🔗 **Target Documentation PR**: [View Documentation Pull Request]({docs_pr_url})")
    elif branch_name:
        lines.append(f"🌿 **Documentation Branch**: `{branch_name}`")
    lines.append("")

    if effective_summary:
        lines.extend([
            "<details>",
            "<summary>📋 Summary of Changes</summary>",
            "",
            effective_summary.strip(),
            "</details>",
            "",
        ])

    run_meta = f"Run ID: `{run_id}` • " if run_id else ""
    lines.append(f"---  \n*{run_meta}Synchronized autonomously by AutoDocs*")

    return "\n".join(lines)


def extract_repo_name(url: str) -> str:
    """
    Extracts 'owner/repo' slug from various Git URL formats.
    e.g. 'https://github.com/org/docs.git' -> 'org/docs'
         'git@github.com:org/docs.git' -> 'org/docs'
         'org/docs' -> 'org/docs'
    """
    cleaned = url.strip()
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if ":" in cleaned and not cleaned.startswith("http"):
        cleaned = cleaned.split(":")[-1]
    elif "/" in cleaned:
        parts = cleaned.split("/")
        if len(parts) >= 2:
            cleaned = f"{parts[-2]}/{parts[-1]}"
    return cleaned
