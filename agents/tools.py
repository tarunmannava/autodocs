from __future__ import annotations

from typing import List

from langchain_core.tools import StructuredTool
from mcp.server.fastmcp import FastMCP


def get_mcp_langchain_tools(
    code_intel_server: FastMCP,
    docs_workspace_server: FastMCP,
) -> List[StructuredTool]:
    """
    Extracts all tools from the Code Intelligence and Docs Workspace FastMCP servers
    and converts them into LangChain-compatible StructuredTool instances for the ReAct agent.

    Args:
        code_intel_server: FastMCP server providing source repository code intelligence.
        docs_workspace_server: FastMCP server providing target documentation workspace operations.

    Returns:
        List[StructuredTool]: Combined list of LangChain tools ready for LLM binding.
    """
    tools: List[StructuredTool] = []

    for server in (code_intel_server, docs_workspace_server):
        for tool_def in server._tool_manager.list_tools():
            tool_fn = tool_def.fn
            st = StructuredTool.from_function(
                func=tool_fn,
                name=tool_def.name,
                description=tool_def.description or "",
            )
            tools.append(st)

    return tools
