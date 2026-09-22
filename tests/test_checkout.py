import subprocess
from pathlib import Path

import pytest

from repo_manager.checkout import CheckoutError, temporary_repository_checkout


def test_temporary_repository_checkout_local_repo(tmp_path: Path):
    # Create a dummy local git repository to test cloning against
    dummy_repo = tmp_path / "dummy_origin"
    dummy_repo.mkdir()
    
    subprocess.run(["git", "init"], cwd=str(dummy_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(dummy_repo), check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(dummy_repo), check=True)
    
    test_file = dummy_repo / "README.md"
    test_file.write_text("# Test Repo\n")
    
    subprocess.run(["git", "add", "."], cwd=str(dummy_repo), check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(dummy_repo), check=True)

    cloned_path = None
    with temporary_repository_checkout(clone_url=str(dummy_repo)) as repo_dir:
        cloned_path = repo_dir
        assert repo_dir.exists()
        assert (repo_dir / "README.md").exists()
        assert (repo_dir / "README.md").read_text() == "# Test Repo\n"

    # Verify automatic cleanup after exiting context
    assert cloned_path is not None
    assert not cloned_path.exists()


def test_temporary_repository_checkout_invalid_url():
    with pytest.raises(CheckoutError):
        with temporary_repository_checkout(clone_url="file:///invalid/local/path/nonexistent.git"):
            pass


def test_temporary_repository_checkout_multi_commit_history(tmp_path: Path):
    """Verify checking out repository with base_sha and head_sha several commits apart."""
    dummy_repo = tmp_path / "multi_commit_origin"
    dummy_repo.mkdir()

    subprocess.run(["git", "init"], cwd=str(dummy_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(dummy_repo), check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(dummy_repo), check=True)

    # Base commit
    test_file = dummy_repo / "doc.txt"
    test_file.write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(dummy_repo), check=True)
    subprocess.run(["git", "commit", "-m", "Commit 1 (base)"], cwd=str(dummy_repo), check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(dummy_repo), check=True, capture_output=True, text=True
    ).stdout.strip()

    # Create 5 intermediate commits
    for i in range(2, 7):
        test_file.write_text(f"v{i}\n")
        subprocess.run(["git", "add", "."], cwd=str(dummy_repo), check=True)
        subprocess.run(["git", "commit", "-m", f"Commit {i}"], cwd=str(dummy_repo), check=True)

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(dummy_repo), check=True, capture_output=True, text=True
    ).stdout.strip()

    # Checkout with base_sha and head_sha
    with temporary_repository_checkout(
        clone_url=str(dummy_repo),
        commit_sha=head_sha,
        base_sha=base_sha,
    ) as repo_dir:
        diff_res = subprocess.run(
            ["git", "diff", f"{base_sha}...{head_sha}"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=True,
        )
        assert "-v1" in diff_res.stdout
        assert "+v6" in diff_res.stdout

