from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

from agents.doc_agent import DocumentationAgent
from agents.guardrails import (
    validate_markdown_syntax,
    validate_relative_links,
    verify_documentation_edits,
)
from agents.llm import get_chat_model
from agents.tools import get_mcp_langchain_tools
from backend.config import Settings
from mcp_server.code_intel_server import create_code_intel_server
from mcp_server.docs_workspace_server import create_docs_workspace_server

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
    """Deterministic mock chat model that issues scripted tool calls before final text."""

    tool_to_call: Optional[str] = None
    tool_args: dict = {}
    step: int = 0

    def _generate(
        self,
        messages: List[Any],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.step += 1
        if self.step == 1 and self.tool_to_call:
            msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": self.tool_to_call,
                        "args": self.tool_args,
                        "id": "mock_call_1",
                    }
                ],
            )
        else:
            msg = AIMessage(content="Updated documentation to reflect the new `cvv` parameter.")
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    @property
    def _llm_type(self) -> str:
        return "mock-tool-calling-model"


@pytest.fixture
def mock_source_repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "payments.py").write_text(
        "def charge_card(card_id: str, amount: float, cvv: str) -> bool:\n    return True\n",
        encoding="utf-8",
    )
    return src


@pytest.fixture
def mock_docs_repo(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "api.md").write_text(
        "# API Reference\nCall `charge_card(card_id, amount)` to charge.\n",
        encoding="utf-8",
    )
    (docs / "guide.md").write_text(
        "# Guide\nSee [API Reference](api.md) for details.\n",
        encoding="utf-8",
    )
    return docs


# ==================== Tool Bridge Tests ====================


def test_tool_bridge_converts_all_mcp_tools(mock_source_repo: Path, mock_docs_repo: Path) -> None:
    code_server = create_code_intel_server(mock_source_repo, raw_diff=SAMPLE_DIFF)
    docs_server = create_docs_workspace_server(mock_docs_repo)

    tools = get_mcp_langchain_tools(code_server, docs_server)
    tool_names = {t.name for t in tools}

    # Verify Code Intel tools are present
    assert "get_diff_summary" in tool_names
    assert "get_impacted_symbols" in tool_names
    assert "get_symbol_info" in tool_names
    assert "get_call_graph" in tool_names

    # Verify Docs Workspace tools are present
    assert "search_docs" in tool_names
    assert "list_doc_files" in tool_names
    assert "read_doc_file" in tool_names
    assert "edit_doc_file" in tool_names
    assert "create_doc_file" in tool_names
    assert len(tools) == 9


# ==================== Guardrails Tests ====================


def test_guardrails_clean_markdown(mock_docs_repo: Path) -> None:
    content = "# Valid Title\n\n```python\nprint('hello')\n```\n\nSee [Guide](guide.md)."
    syntax_errs = validate_markdown_syntax(content)
    assert len(syntax_errs) == 0

    link_errs = validate_relative_links(mock_docs_repo, "api.md", content)
    assert len(link_errs) == 0


def test_guardrails_unclosed_code_fence() -> None:
    bad_content = "# Title\n```python\nx = 1\n# forgot to close backticks"
    errors = validate_markdown_syntax(bad_content)
    assert len(errors) == 1
    assert "Unclosed code fence" in errors[0]


def test_guardrails_broken_relative_link(mock_docs_repo: Path) -> None:
    broken_content = "See [Missing Page](non_existent_page.md) for info."
    errors = validate_relative_links(mock_docs_repo, "api.md", broken_content)
    assert len(errors) == 1
    assert "does not exist on disk" in errors[0]


def test_guardrails_external_and_anchor_links_ignored(mock_docs_repo: Path) -> None:
    content = "Links: [External](https://google.com) and [Section](#payments) and [Mail](mailto:info@ex.com)"
    errors = validate_relative_links(mock_docs_repo, "api.md", content)
    assert len(errors) == 0


def test_verify_documentation_edits_e2e(mock_docs_repo: Path) -> None:
    res = verify_documentation_edits(mock_docs_repo, ["api.md", "guide.md"])
    assert res.passed is True
    assert len(res.errors) == 0


# ==================== LLM Factory Tests ====================


def test_llm_factory_openrouter_configuration() -> None:
    custom_settings = Settings(
        openrouter_api_key="test-key-123",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_model="meta/muse-spark-1.3-contributor",
        openrouter_reasoning_effort="high",
    )
    llm = get_chat_model(settings=custom_settings, temperature=0.2)
    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "meta/muse-spark-1.3-contributor"
    assert llm.openai_api_base == "https://openrouter.ai/api/v1"
    assert llm.temperature == 0.2
    assert llm.model_kwargs.get("extra_body") == {"reasoning": {"effort": "high"}} or getattr(
        llm, "extra_body", {}
    ) == {"reasoning": {"effort": "high"}}


# ==================== Agent ReAct Execution Tests ====================


def test_agent_executes_tool_call_and_updates_doc_on_disk(
    mock_source_repo: Path,
    mock_docs_repo: Path,
) -> None:
    code_server = create_code_intel_server(mock_source_repo, raw_diff=SAMPLE_DIFF)
    docs_server = create_docs_workspace_server(mock_docs_repo)

    # Script mock model to invoke edit_doc_file
    mock_model = MockToolCallingChatModel(
        tool_to_call="edit_doc_file",
        tool_args={
            "file_path": "api.md",
            "target_block": "Call `charge_card(card_id, amount)` to charge.",
            "replacement_block": "Call `charge_card(card_id, amount, cvv)` to charge.",
        },
    )

    agent = DocumentationAgent(model=mock_model)
    result = agent.run(
        code_intel_server=code_server,
        docs_workspace_server=docs_server,
        docs_dir=mock_docs_repo,
        repo_name="org/payments",
        pr_number=42,
        pr_title="Add CVV verification",
    )

    assert result.success is True
    assert "api.md" in result.modified_files
    assert result.guardrail_result is not None
    assert result.guardrail_result.passed is True
    assert "cvv" in result.summary.lower()

    # Verify the documentation file on disk was modified by the agent's tool call
    updated_text = (mock_docs_repo / "api.md").read_text(encoding="utf-8")
    assert "charge_card(card_id, amount, cvv)" in updated_text


def test_agent_guardrail_failure_detected(
    mock_source_repo: Path,
    mock_docs_repo: Path,
) -> None:
    code_server = create_code_intel_server(mock_source_repo, raw_diff=SAMPLE_DIFF)
    docs_server = create_docs_workspace_server(mock_docs_repo)

    # Script mock model to introduce an unclosed code fence
    mock_model = MockToolCallingChatModel(
        tool_to_call="edit_doc_file",
        tool_args={
            "file_path": "api.md",
            "target_block": "Call `charge_card(card_id, amount)` to charge.",
            "replacement_block": "```python\n# Missing closing fence",
        },
    )

    agent = DocumentationAgent(model=mock_model)
    result = agent.run(
        code_intel_server=code_server,
        docs_workspace_server=docs_server,
        docs_dir=mock_docs_repo,
        repo_name="org/payments",
        pr_number=42,
    )

    # Guardrail must reject the unclosed code block
    assert result.success is False
    assert result.guardrail_result is not None
    assert result.guardrail_result.passed is False
    assert any("Unclosed code fence" in err for err in result.guardrail_result.errors)
