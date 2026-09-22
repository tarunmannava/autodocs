"""Model Context Protocol (MCP) servers and utilities for AutoDocs."""

from mcp_server.code_intel_server import create_code_intel_server
from mcp_server.docs_workspace_server import create_docs_workspace_server
from mcp_server.utils import normalize_newlines, resolve_safe_path

__all__ = [
    "create_code_intel_server",
    "create_docs_workspace_server",
    "normalize_newlines",
    "resolve_safe_path",
]
