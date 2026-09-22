from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from mcp.server.fastmcp import FastMCP

from agents.guardrails import GuardrailResult, verify_documentation_edits
from agents.llm import get_chat_model
from agents.tools import get_mcp_langchain_tools

logger = logging.getLogger(__name__)

DOCUMENTATION_AGENT_SYSTEM_PROMPT = """You are AutoDocs, an expert technical documentation engineer.
Your job is to synchronize technical documentation in a Target Documentation Repository
when a Pull Request is merged in a Source Code Repository.

You have access to two sets of tools:
1. SOURCE CODE INTELLIGENCE TOOLS:
   - get_diff_summary: View which files and line hunks changed in the PR.
   - get_impacted_symbols: Intersect diff lines with AST to find modified functions, classes,
     and routes with downstream callers.
   - get_symbol_info: Inspect detailed signatures, parameters, return types, and docstrings of any symbol.
   - get_call_graph: Check callers and callees of symbols.

2. TARGET DOCS WORKSPACE TOOLS:
   - search_docs: Search the documentation repository for references to modified functions, endpoints, or concepts.
   - list_doc_files: Explore the documentation repository structure.
   - read_doc_file: Read existing documentation pages to inspect context.
   - edit_doc_file: Apply precise replacements to update documentation.
   - create_doc_file: Create new documentation pages when new features are added.

WORKFLOW:
1. First, call `get_diff_summary` and `get_impacted_symbols` to thoroughly understand what changed in the code.
2. Next, for any public API, endpoint, or behavior change, use `search_docs` to find matching pages
   in the documentation repository.
3. Read the candidate documentation pages using `read_doc_file`.
4. If documentation updates are needed, use `edit_doc_file` (with exact context lines) or `create_doc_file`.
5. If the changes are internal-only or do not affect any documentation, do NOT edit docs unnecessarily.
6. Conclude with a clear, concise summary of the documentation changes made and the rationale.
"""


@dataclass
class AgentRunResult:
    """Represents the complete execution output of the Documentation Agent."""

    success: bool
    summary: str
    modified_files: List[str] = field(default_factory=list)
    guardrail_result: Optional[GuardrailResult] = None
    messages: List[Any] = field(default_factory=list)
    error_message: Optional[str] = None


class DocumentationAgent:
    """
    Autonomous ReAct agent that inspects code changes in a source repository
    and applies documentation updates in a target documentation repository.
    """

    def __init__(
        self,
        model: Optional[BaseChatModel] = None,
        system_prompt: Optional[str] = None,
    ) -> None:
        self.model = model or get_chat_model()
        self.system_prompt = system_prompt or DOCUMENTATION_AGENT_SYSTEM_PROMPT

    def run(
        self,
        code_intel_server: FastMCP,
        docs_workspace_server: FastMCP,
        docs_dir: Path | str,
        repo_name: str = "source-repo",
        pr_number: int = 1,
        pr_title: str = "",
        pr_description: str = "",
    ) -> AgentRunResult:
        """
        Executes the autonomous documentation agent loop across the source and docs repositories.

        Args:
            code_intel_server: FastMCP server providing source code intelligence.
            docs_workspace_server: FastMCP server providing docs workspace operations.
            docs_dir: Local path to the target documentation repository root.
            repo_name: Name of the source code repository.
            pr_number: Pull request number.
            pr_title: Pull request title.
            pr_description: Pull request description body.

        Returns:
            AgentRunResult: Final outcome containing modified files, summary, and guardrail validation.
        """
        docs_root = Path(docs_dir).resolve()

        tools = get_mcp_langchain_tools(
            code_intel_server=code_intel_server,
            docs_workspace_server=docs_workspace_server,
        )

        agent_executor = create_agent(
            model=self.model,
            tools=tools,
            system_prompt=self.system_prompt,
        )

        initial_prompt = (
            f"A Pull Request has been merged in source repository '{repo_name}'.\n"
            f"PR Number: #{pr_number}\n"
            f"Title: {pr_title or 'Code updates'}\n"
            f"Description: {pr_description or 'No description provided.'}\n\n"
            "Please analyze the code changes using your Code Intelligence tools, determine what documentation "
            "in the documentation repository needs to be updated or created, and apply the updates."
        )

        try:
            state = agent_executor.invoke({"messages": [HumanMessage(content=initial_prompt)]})
            messages = state.get("messages", [])
        except Exception as exc:
            logger.error(f"Error executing documentation agent loop: {exc or exc.__class__.__name__}", exc_info=True)
            return AgentRunResult(
                success=False,
                summary="Agent execution failed with an unhandled exception.",
                error_message=str(exc) or exc.__class__.__name__,
            )

        # Track files modified or created by the agent via tool calls
        modified_files: List[str] = []
        for msg in messages:
            tool_calls = getattr(msg, "tool_calls", None) or []
            for call in tool_calls:
                fn_name = call.get("name")
                args = call.get("args") or {}
                if fn_name in {"edit_doc_file", "create_doc_file"}:
                    file_path = args.get("file_path")
                    if file_path and file_path not in modified_files:
                        modified_files.append(file_path)

        # Execute deterministic post-edit guardrail verification
        guardrail_result = verify_documentation_edits(docs_root, modified_files)

        # Extract final agent summary text from last AIMessage
        final_summary = "Documentation processing completed."
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                if isinstance(msg.content, str):
                    final_summary = msg.content
                elif isinstance(msg.content, list):
                    # Handle multimodal/content block lists
                    text_parts = [part.get("text", "") for part in msg.content if isinstance(part, dict)]
                    final_summary = "\n".join(filter(None, text_parts)) or final_summary
                break

        return AgentRunResult(
            success=guardrail_result.passed,
            summary=final_summary,
            modified_files=modified_files,
            guardrail_result=guardrail_result,
            messages=messages,
        )
