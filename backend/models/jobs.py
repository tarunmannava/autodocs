from dataclasses import dataclass


@dataclass(frozen=True)
class PullRequestJob:
    delivery_id: str
    repository: str
    pr_number: int
    head_sha: str
    base_sha: str
