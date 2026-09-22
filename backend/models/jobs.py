from dataclasses import dataclass


@dataclass(frozen=True)
class PullRequestJob:
    delivery_id: str
    repository: str
    pr_number: int
    head_sha: str
    base_sha: str
    job_id: str | None = None
    clone_url: str | None = None
    diff_text: str | None = None
    pr_title: str | None = None
    docs_repo_url: str | None = None
    docs_repo_branch: str | None = None
