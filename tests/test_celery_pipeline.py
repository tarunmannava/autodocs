from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agents.doc_agent import DocumentationAgent
from backend.accessors.run import InMemoryRunAccessor
from backend.jobs.tasks import process_pr_event
from backend.models.enums import RunStatus

SAMPLE_DIFF = """diff --git a/payments.py b/payments.py
index 1234567..89abcde 100644
--- a/payments.py
+++ b/payments.py
@@ -1,2 +1,2 @@
-def charge_card(card_id: str, amount: float) -> bool:
+def charge_card(card_id: str, amount: float, cvv: str) -> bool:
     return True
"""


class MockToolCallingChatModel(BaseChatModel):
    """Deterministic mock chat model that issues scripted tool calls."""

    tool_to_call: Optional[str] = None
    tool_args: dict = {}
    step: int = 0

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    @property
    def _llm_type(self) -> str:
        return "mock_tool_calling_chat_model"

    def _generate(
        self,
        messages: list[Any],
        stop: Optional[list[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.step += 1
        if self.step == 1 and self.tool_to_call:
            tool_call = {
                "name": self.tool_to_call,
                "args": self.tool_args,
                "id": "call_mock_123",
            }
            msg = AIMessage(content="", tool_calls=[tool_call])
            return ChatResult(generations=[ChatGeneration(message=msg)])

        msg = AIMessage(content="Updated documentation to add the cvv parameter.")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def setup_git_repo(path: Path) -> None:
    """Helper to initialize a bare minimum git repo for testing."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=str(path), check=True)


def test_process_pr_event_e2e_successful_sync(tmp_path: Path) -> None:
    source_dir = tmp_path / "source_repo"
    docs_dir = tmp_path / "docs_repo"

    setup_git_repo(source_dir)
    setup_git_repo(docs_dir)

    # Populate source repo
    payments_file = source_dir / "payments.py"
    payments_file.write_text("def charge_card(card_id: str, amount: float, cvv: str) -> bool:\n    return True\n")
    subprocess.run(["git", "add", "."], cwd=str(source_dir), check=True)
    subprocess.run(["git", "commit", "-m", "init source"], cwd=str(source_dir), check=True)

    # Populate docs repo
    api_doc = docs_dir / "api.md"
    api_doc.write_text("# Payments API\n\nCall `charge_card(card_id, amount)` to charge.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(docs_dir), check=True)
    subprocess.run(["git", "commit", "-m", "init docs"], cwd=str(docs_dir), check=True)

    run_accessor = InMemoryRunAccessor()
    job_payload = {
        "job_id": "pipeline-job-123",
        "repository": "org/payments",
        "pr_number": 42,
        "pr_title": "Add CVV verification",
        "diff_text": SAMPLE_DIFF,
        "source_dir": str(source_dir),
        "docs_dir": str(docs_dir),
    }

    mock_model = MockToolCallingChatModel(
        tool_to_call="edit_doc_file",
        tool_args={
            "file_path": "api.md",
            "target_block": "Call `charge_card(card_id, amount)` to charge.",
            "replacement_block": "Call `charge_card(card_id, amount, cvv)` to charge.",
        },
    )
    agent = DocumentationAgent(model=mock_model)

    result = process_pr_event.apply(
        args=[job_payload],
        kwargs={"run_accessor": run_accessor, "agent": agent},
    ).get()

    assert result["status"] == "success"
    assert result["job_id"] == "pipeline-job-123"
    assert "api.md" in result["modified_files"]
    assert result["branch_name"] == "docs/sync-org-payments-pr-42"
    assert result["commit_sha"] != ""

    # Verify doc on disk was modified
    updated_doc = api_doc.read_text(encoding="utf-8")
    assert "charge_card(card_id, amount, cvv)" in updated_doc

    # Verify run accessor status and completed_at timestamp
    record = run_accessor.runs[0] if run_accessor.runs else None
    assert record is not None
    assert record.status == RunStatus.PUBLISHED
    assert record.completed_at is not None


def test_process_pr_event_e2e_skipped_when_no_modifications(tmp_path: Path) -> None:
    source_dir = tmp_path / "source_repo_skip"
    docs_dir = tmp_path / "docs_repo_skip"

    setup_git_repo(source_dir)
    setup_git_repo(docs_dir)

    payments_file = source_dir / "payments.py"
    payments_file.write_text("def internal_helper(): pass\n")
    subprocess.run(["git", "add", "."], cwd=str(source_dir), check=True)
    subprocess.run(["git", "commit", "-m", "init source"], cwd=str(source_dir), check=True)

    api_doc = docs_dir / "api.md"
    api_doc.write_text("# Payments API\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(docs_dir), check=True)
    subprocess.run(["git", "commit", "-m", "init docs"], cwd=str(docs_dir), check=True)

    run_accessor = InMemoryRunAccessor()
    job_payload = {
        "job_id": "pipeline-job-skip",
        "repository": "org/payments",
        "pr_number": 99,
        "diff_text": SAMPLE_DIFF,
        "source_dir": str(source_dir),
        "docs_dir": str(docs_dir),
    }

    # Model does not call any tool, returns plain text
    mock_model = MockToolCallingChatModel(tool_to_call=None)
    agent = DocumentationAgent(model=mock_model)

    result = process_pr_event.apply(
        args=[job_payload],
        kwargs={"run_accessor": run_accessor, "agent": agent},
    ).get()

    assert result["status"] == "skipped"
    assert result["reason"] == "no_documentation_changes_needed"
    assert result["modified_files"] == []

    record = run_accessor.runs[0] if run_accessor.runs else None
    assert record is not None
    assert record.status == RunStatus.SKIPPED
    assert record.completed_at is not None


def test_process_pr_event_e2e_guardrail_failure(tmp_path: Path) -> None:
    source_dir = tmp_path / "source_repo_fail"
    docs_dir = tmp_path / "docs_repo_fail"

    setup_git_repo(source_dir)
    setup_git_repo(docs_dir)

    payments_file = source_dir / "payments.py"
    payments_file.write_text("def charge_card(): pass\n")
    subprocess.run(["git", "add", "."], cwd=str(source_dir), check=True)
    subprocess.run(["git", "commit", "-m", "init source"], cwd=str(source_dir), check=True)

    api_doc = docs_dir / "api.md"
    api_doc.write_text("# Payments API\n\nTarget line to replace.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(docs_dir), check=True)
    subprocess.run(["git", "commit", "-m", "init docs"], cwd=str(docs_dir), check=True)

    run_accessor = InMemoryRunAccessor()
    job_payload = {
        "job_id": "pipeline-job-fail",
        "repository": "org/payments",
        "pr_number": 55,
        "diff_text": SAMPLE_DIFF,
        "source_dir": str(source_dir),
        "docs_dir": str(docs_dir),
    }

    # Model introduces unclosed code fence
    mock_model = MockToolCallingChatModel(
        tool_to_call="edit_doc_file",
        tool_args={
            "file_path": "api.md",
            "target_block": "Target line to replace.",
            "replacement_block": "```python\n# Broken fence",
        },
    )
    agent = DocumentationAgent(model=mock_model)

    result = process_pr_event.apply(
        args=[job_payload],
        kwargs={"run_accessor": run_accessor, "agent": agent},
    ).get()

    assert result["status"] == "failed"
    assert "Guardrail violations" in result["error"]

    record = run_accessor.runs[0] if run_accessor.runs else None
    assert record is not None
    assert record.status == RunStatus.FAILED
    assert "Guardrail violations" in (record.error_message or "")
    assert record.completed_at is not None
