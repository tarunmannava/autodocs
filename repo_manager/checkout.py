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


@contextmanager
def temporary_repository_checkout(
    clone_url: str,
    commit_sha: str | None = None,
    base_sha: str | None = None,
    branch: str | None = None,
    token: str | None = None,
) -> Generator[Path, None, None]:
    """
    Context manager that clones a git repository into a temporary directory,
    fetches required base and head commits, and cleans it up automatically when exiting.

    Args:
        clone_url: HTTPS or local clone URL for the repository.
        commit_sha: Specific commit SHA to checkout after clone (optional).
        base_sha: Base commit SHA to ensure fetched for diffing (optional).
        branch: Specific branch name to clone (optional).
        token: Authentication token to inject into HTTPS URL (optional).

    Yields:
        Path: Absolute path to the checked-out repository directory.
    """
    target_url = clone_url
    if token and clone_url.startswith("https://"):
        target_url = clone_url.replace("https://", f"https://x-access-token:{token}@")

    temp_dir = tempfile.mkdtemp(prefix="autodocs_repo_")
    repo_path = Path(temp_dir)

    try:
        cmd = ["git", "clone"]
        if branch:
            cmd.extend(["--branch", branch])
        cmd.extend([target_url, str(repo_path)])

        logger.info(f"Cloning repository {clone_url} into {repo_path}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            raise CheckoutError(f"Failed to clone repository: {result.stderr.strip()}")

        if base_sha:
            fetch_base_cmd = ["git", "fetch", "origin", base_sha]
            subprocess.run(fetch_base_cmd, cwd=str(repo_path), capture_output=True, text=True, check=False)

        if commit_sha:
            fetch_cmd = ["git", "fetch", "origin", commit_sha]
            subprocess.run(fetch_cmd, cwd=str(repo_path), capture_output=True, text=True, check=False)

            checkout_cmd = ["git", "checkout", commit_sha]
            co_result = subprocess.run(checkout_cmd, cwd=str(repo_path), capture_output=True, text=True, check=False)
            if co_result.returncode != 0:
                logger.warning(f"Could not checkout specific SHA {commit_sha}, using default HEAD.")

        yield repo_path

    finally:
        logger.info(f"Cleaning up temporary repository directory at {repo_path}")
        
        def _remove_readonly(func, path, exc_info):
            import stat
            os.chmod(path, stat.S_IWRITE)
            func(path)

        if repo_path.exists():
            shutil.rmtree(repo_path, onerror=_remove_readonly)

