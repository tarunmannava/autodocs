import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)


class CheckoutError(Exception):
    """Raised when repository checkout fails."""
    pass


def _sanitize_url(url: str) -> str:
    """Masks authentication credentials in git URLs for safe logging."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            _, host_part = rest.split("@", 1)
            return f"{scheme}://***@{host_part}"
    return url


@contextmanager
def temporary_repository_checkout(
    clone_url: str,
    commit_sha: str | None = None,
    base_sha: str | None = None,
    branch: str | None = None,
    token: str | None = None,
    initial_depth: int = 2,
    max_deepen_attempts: int = 5,
) -> Generator[Path, None, None]:
    """
    Context manager that performs a shallow clone of a git repository into a temporary directory,
    fetches required base and head commits with shallow depth (progressively deepening if the PR
    contains more commits than initial_depth to establish merge-base), and cleans it up automatically.

    Args:
        clone_url: HTTPS or local clone URL for the repository.
        commit_sha: Specific commit SHA to checkout after clone (optional).
        base_sha: Base commit SHA to ensure fetched for diffing (optional).
        branch: Specific branch name to clone (optional).
        token: Authentication token to inject into HTTPS URL (optional).
        initial_depth: Initial fetch depth for target commits (default 2).
        max_deepen_attempts: Maximum attempts to deepen shallow history if merge-base is missing.

    Yields:
        Path: Absolute path to the checked-out repository directory.
    """
    target_url = clone_url
    if token and clone_url.startswith("https://"):
        target_url = clone_url.replace("https://", f"https://x-access-token:{token}@")

    temp_dir = tempfile.mkdtemp(prefix="autodocs_repo_")
    repo_path = Path(temp_dir)

    try:
        cmd = ["git", "clone", "--depth", "1"]
        # In local filesystem clones, git ignores --depth unless --no-local is specified
        if not target_url.startswith(("http://", "https://", "git@", "ssh://", "file://")):
            cmd.append("--no-local")
        if branch:
            cmd.extend(["--branch", branch])
        cmd.extend([target_url, str(repo_path)])

        safe_log_url = _sanitize_url(target_url)
        logger.info(f"Cloning repository (shallow --depth 1) {safe_log_url} into {repo_path}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            clean_stderr = result.stderr.replace(target_url, safe_log_url)
            raise CheckoutError(f"Failed to clone repository: {clean_stderr.strip()}")

        # Fetch base_sha if provided
        if base_sha:
            fetch_base_cmd = ["git", "fetch", f"--depth={initial_depth}", "origin", base_sha]
            subprocess.run(fetch_base_cmd, cwd=str(repo_path), capture_output=True, text=True, check=False)

        # Fetch commit_sha if provided
        if commit_sha:
            fetch_cmd = ["git", "fetch", f"--depth={initial_depth}", "origin", commit_sha]
            subprocess.run(fetch_cmd, cwd=str(repo_path), capture_output=True, text=True, check=False)

            checkout_cmd = ["git", "checkout", commit_sha]
            co_result = subprocess.run(checkout_cmd, cwd=str(repo_path), capture_output=True, text=True, check=False)
            if co_result.returncode != 0:
                logger.warning(f"Could not checkout specific SHA {commit_sha}, using default HEAD.")

        # If both base_sha and commit_sha are provided and differ, ensure merge-base exists
        # by deepening shallow history if the PR has more commits than initial_depth
        if base_sha and commit_sha and base_sha != commit_sha:
            mb_check = subprocess.run(
                ["git", "merge-base", base_sha, commit_sha],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                check=False,
            )
            if mb_check.returncode != 0:
                logger.info(
                    f"Merge-base between {base_sha} and {commit_sha} not found at depth {initial_depth}. "
                    "PR has more commits; progressively deepening history..."
                )
                step = 10
                for attempt in range(max_deepen_attempts):
                    subprocess.run(
                        ["git", "fetch", f"--deepen={step}", "origin", commit_sha],
                        cwd=str(repo_path),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    subprocess.run(
                        ["git", "fetch", f"--deepen={step}", "origin", base_sha],
                        cwd=str(repo_path),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    mb_check = subprocess.run(
                        ["git", "merge-base", base_sha, commit_sha],
                        cwd=str(repo_path),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if mb_check.returncode == 0:
                        logger.info(f"Merge-base found after deepening attempt {attempt + 1} (step={step}).")
                        break
                    step *= 2

                # If still no merge-base after deepening iterations, fallback to unshallow
                if mb_check.returncode != 0:
                    logger.warning(
                        "Merge-base not reached through shallow deepening; fetching unshallow history as fallback."
                    )
                    subprocess.run(
                        ["git", "fetch", "--unshallow"],
                        cwd=str(repo_path),
                        capture_output=True,
                        text=True,
                        check=False,
                    )

        yield repo_path

    finally:
        logger.info(f"Cleaning up temporary repository directory at {repo_path}")

        def _remove_readonly(func, path, exc_info):
            import stat
            os.chmod(path, stat.S_IWRITE)
            func(path)

        if repo_path.exists():
            shutil.rmtree(repo_path, onerror=_remove_readonly)

