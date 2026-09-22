from __future__ import annotations

import subprocess
from pathlib import Path

import httpx

from backend.services.publisher import (
    create_docs_branch_and_commit,
    extract_repo_name,
    format_source_pr_comment,
    open_documentation_pr,
    post_source_pr_comment,
    push_docs_branch,
)


def test_extract_repo_name() -> None:
    assert extract_repo_name("https://github.com/octo-org/developer-docs.git") == "octo-org/developer-docs"
    assert extract_repo_name("https://github.com/octo-org/developer-docs") == "octo-org/developer-docs"
    assert extract_repo_name("git@github.com:octo-org/developer-docs.git") == "octo-org/developer-docs"
    assert extract_repo_name("octo-org/developer-docs") == "octo-org/developer-docs"


def test_create_docs_branch_and_commit_success(tmp_path: Path) -> None:
    repo_dir = tmp_path / "docs_repo"
    repo_dir.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=str(repo_dir), check=True)

    # Initial file and commit
    index_file = repo_dir / "index.md"
    index_file.write_text("# Initial Docs\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True)

    # Modify file
    index_file.write_text("# Initial Docs\n\nUpdated by AutoDocs.\n", encoding="utf-8")

    commit_sha = create_docs_branch_and_commit(
        docs_dir=repo_dir,
        branch_name="docs/update-payments-pr-42",
        commit_message="docs: sync payments api",
        author_name="AutoDocs Agent",
        author_email="agent@autodocs.dev",
    )

    assert commit_sha != ""
    assert len(commit_sha) == 40

    # Verify branch was checked out and commit created
    branch_res = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo_dir), capture_output=True, text=True, check=True
    )
    assert branch_res.stdout.strip() == "docs/update-payments-pr-42"

    log_res = subprocess.run(
        ["git", "log", "-1", "--pretty=format:%an <%ae> - %s"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "AutoDocs Agent <agent@autodocs.dev> - docs: sync payments api" in log_res.stdout


def test_create_docs_branch_and_commit_no_changes(tmp_path: Path) -> None:
    repo_dir = tmp_path / "docs_repo_clean"
    repo_dir.mkdir()

    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=str(repo_dir), check=True)

    index_file = repo_dir / "index.md"
    index_file.write_text("# Docs\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "clean commit"], cwd=str(repo_dir), check=True)

    # No files modified!
    commit_sha = create_docs_branch_and_commit(
        docs_dir=repo_dir,
        branch_name="docs/clean-branch",
        commit_message="docs: should not commit",
    )
    assert commit_sha == ""


def test_push_docs_branch_success(tmp_path: Path) -> None:
    # Setup bare upstream and local clone
    bare_remote = tmp_path / "bare_remote.git"
    subprocess.run(["git", "init", "--bare", str(bare_remote)], check=True, capture_output=True)

    local_repo = tmp_path / "local_repo"
    subprocess.run(["git", "clone", str(bare_remote), str(local_repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(local_repo), check=True)
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=str(local_repo), check=True)

    test_file = local_repo / "guide.md"
    test_file.write_text("# Guide\n", encoding="utf-8")
    subprocess.run(["git", "checkout", "-b", "docs/test-branch"], cwd=str(local_repo), check=True)
    subprocess.run(["git", "add", "."], cwd=str(local_repo), check=True)
    subprocess.run(["git", "commit", "-m", "add guide"], cwd=str(local_repo), check=True)

    success = push_docs_branch(docs_dir=local_repo, branch_name="docs/test-branch", remote="origin")
    assert success is True


def test_open_documentation_pr_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/repos/org/developer-docs/pulls"
        assert request.headers["Authorization"] == "Bearer mock-token-xyz"
        return httpx.Response(201, json={"html_url": "https://github.com/org/developer-docs/pull/99"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    pr_url = open_documentation_pr(
        docs_repo="org/developer-docs",
        branch_name="docs/update-auth-pr-10",
        base_branch="main",
        title="docs: update auth",
        body="Sync auth docs",
        github_token="mock-token-xyz",
        client=client,
    )

    assert pr_url == "https://github.com/org/developer-docs/pull/99"


def test_open_documentation_pr_already_exists_recovers_existing_pr() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            # 422 Unprocessable Entity
            return httpx.Response(422, json={"message": "A pull request already exists for org:docs/branch."})
        if request.method == "GET":
            # List existing PRs
            return httpx.Response(200, json=[{"html_url": "https://github.com/org/developer-docs/pull/55"}])
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    pr_url = open_documentation_pr(
        docs_repo="org/developer-docs",
        branch_name="docs/branch",
        github_token="mock-token-xyz",
        client=client,
    )

    assert pr_url == "https://github.com/org/developer-docs/pull/55"


def test_open_documentation_pr_missing_token_returns_none() -> None:
    result = open_documentation_pr(
        docs_repo="org/developer-docs",
        branch_name="docs/branch",
        github_token="",
    )
    assert result is None


def test_post_source_pr_comment_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/repos/org/source-repo/issues/42/comments"
        return httpx.Response(201, json={"id": 12345})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    posted = post_source_pr_comment(
        source_repo="org/source-repo",
        pr_number=42,
        comment_body="Hello world",
        github_token="mock-token-xyz",
        client=client,
    )
    assert posted is True


def test_format_source_pr_comment() -> None:
    comment = format_source_pr_comment(
        modified_files=["docs/api/payments.md", "docs/guides/checkout.md"],
        docs_pr_url="https://github.com/org/docs/pull/12",
        branch_name="docs/sync-payments-pr-42",
        summary="Added CVV parameter to charge_card() function.",
        run_id="run-uuid-789",
    )

    assert "AutoDocs: Documentation Synchronization" in comment
    assert "`docs/api/payments.md`" in comment
    assert "`docs/guides/checkout.md`" in comment
    assert "[View Documentation Pull Request](https://github.com/org/docs/pull/12)" in comment
    assert "Added CVV parameter" in comment
    assert "Run ID: `run-uuid-789`" in comment
