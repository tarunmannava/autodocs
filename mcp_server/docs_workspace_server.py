from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from mcp_server.utils import normalize_newlines, resolve_safe_path

DOCS_EXTENSIONS = {".md", ".mdx", ".rst", ".txt", ".json", ".yaml", ".yml"}
IGNORED_DIRECTORIES = {".git", ".github", "node_modules", "venv", ".venv", "__pycache__", "build", "dist"}


def create_docs_workspace_server(
    docs_dir: Path | str,
    server_name: str = "docs-workspace",
) -> FastMCP:
    """
    Creates and configures a Docs Workspace FastMCP server bound to the target documentation directory.

    Args:
        docs_dir: Path to the root of the target documentation repository.
        server_name: MCP server name identifier.

    Returns:
        FastMCP: Configured server instance with documentation file and search tools.
    """
    docs_path = Path(docs_dir).resolve()
    docs_path.mkdir(parents=True, exist_ok=True)

    server = FastMCP(server_name)

    @server.tool()
    def search_docs(query: str, max_results: int = 15) -> Dict[str, Any]:
        """
        Searches all markdown, MDX, and text documentation files in the workspace
        for occurrences of the query string (e.g. function name, route path, or concept).

        Args:
            query: Literal search string (case-insensitive).
            max_results: Maximum number of matches to return.
        """
        if not query.strip():
            return {"query": query, "total_matches": 0, "matches": []}

        matches: List[Dict[str, Any]] = []
        lower_query = query.lower()

        for root, dirs, files in os.walk(docs_path):
            # Prune ignored directories
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRECTORIES]

            for file_name in sorted(files):
                ext = Path(file_name).suffix.lower()
                if ext not in DOCS_EXTENSIONS:
                    continue

                full_path = Path(root) / file_name
                rel_path = full_path.relative_to(docs_path).as_posix()

                try:
                    text = full_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                lines = normalize_newlines(text).splitlines()
                for idx, line in enumerate(lines):
                    if lower_query in line.lower():
                        # Extract 3-line snippet context
                        snippet_start = max(0, idx - 1)
                        snippet_end = min(len(lines), idx + 2)
                        snippet = "\n".join(lines[snippet_start:snippet_end])

                        matches.append(
                            {
                                "file_path": rel_path,
                                "line_number": idx + 1,
                                "line_content": line.strip(),
                                "snippet": snippet,
                            }
                        )
                        if len(matches) >= max_results:
                            break

                if len(matches) >= max_results:
                    break

        return {
            "query": query,
            "total_matches": len(matches),
            "matches": matches,
        }

    @server.tool()
    def list_doc_files(subfolder: str = "") -> Dict[str, Any]:
        """
        Lists all documentation files in the repository workspace or a subfolder.

        Args:
            subfolder: Optional subfolder path relative to the documentation root.
        """
        try:
            target_dir = resolve_safe_path(docs_path, subfolder)
        except PermissionError as exc:
            return {"error": str(exc)}

        if not target_dir.exists() or not target_dir.is_dir():
            return {"error": f"Directory not found: '{subfolder}'"}

        file_list: List[Dict[str, Any]] = []
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRECTORIES]
            for file_name in sorted(files):
                ext = Path(file_name).suffix.lower()
                if ext in DOCS_EXTENSIONS:
                    full_path = Path(root) / file_name
                    rel_path = full_path.relative_to(docs_path).as_posix()
                    file_list.append(
                        {
                            "path": rel_path,
                            "size_bytes": full_path.stat().st_size,
                        }
                    )

        return {"files": file_list, "total_files": len(file_list)}

    @server.tool()
    def read_doc_file(
        file_path: str,
        start_line: int = 1,
        end_line: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Reads the content of a documentation file, with optional line range pagination.

        Args:
            file_path: Relative path to the document inside the documentation workspace.
            start_line: Starting line (1-indexed, inclusive).
            end_line: Optional ending line (1-indexed, inclusive).
        """
        try:
            safe_file = resolve_safe_path(docs_path, file_path)
        except PermissionError as exc:
            return {"error": str(exc)}

        if not safe_file.exists():
            return {"error": f"File not found: '{file_path}'"}
        if not safe_file.is_file():
            return {"error": f"Path is a directory, not a file: '{file_path}'"}

        try:
            raw_text = safe_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"error": f"Could not read file: {exc}"}

        lines = normalize_newlines(raw_text).splitlines()
        total_lines = len(lines)

        s_idx = max(0, start_line - 1)
        e_idx = min(total_lines, end_line) if end_line is not None else total_lines

        selected_content = "\n".join(lines[s_idx:e_idx])

        return {
            "file_path": safe_file.relative_to(docs_path).as_posix(),
            "total_lines": total_lines,
            "start_line": start_line,
            "end_line": e_idx,
            "content": selected_content,
        }

    @server.tool()
    def edit_doc_file(
        file_path: str,
        target_block: str,
        replacement_block: str,
    ) -> Dict[str, Any]:
        """
        Applies a precise replacement edit to an existing documentation file.
        Fails safely if target_block is not found or if multiple matches are found.

        Args:
            file_path: Relative path to the documentation file.
            target_block: Exact text block to be replaced.
            replacement_block: Exact replacement text.
        """
        try:
            safe_file = resolve_safe_path(docs_path, file_path)
        except PermissionError as exc:
            return {"success": False, "error": str(exc)}

        if not safe_file.exists():
            return {"success": False, "error": f"File not found: '{file_path}'"}
        if not safe_file.is_file():
            return {"success": False, "error": f"Path is a directory, not a file: '{file_path}'"}

        try:
            raw_text = safe_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"success": False, "error": f"Could not read file: {exc}"}

        norm_text = normalize_newlines(raw_text)
        norm_target = normalize_newlines(target_block)
        norm_replacement = normalize_newlines(replacement_block)

        match_count = norm_text.count(norm_target)
        if match_count == 0:
            return {
                "success": False,
                "error": f"target_block not found in '{file_path}'. Ensure exact character and whitespace match.",
            }
        if match_count > 1:
            return {
                "success": False,
                "error": (
                    f"target_block found {match_count} times in '{file_path}'. "
                    "Provide more surrounding context lines to uniquely identify the section."
                ),
            }

        updated_text = norm_text.replace(norm_target, norm_replacement, 1)

        try:
            safe_file.write_text(updated_text, encoding="utf-8")
        except OSError as exc:
            return {"success": False, "error": f"Could not write file: {exc}"}

        return {
            "success": True,
            "file_path": safe_file.relative_to(docs_path).as_posix(),
            "lines_modified": len(norm_replacement.splitlines()),
        }

    @server.tool()
    def create_doc_file(
        file_path: str,
        content: str,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """
        Creates a new documentation file, automatically generating any missing parent directories.

        Args:
            file_path: Relative path for the new file.
            content: Initial content for the file.
            overwrite: If False, errors when the file already exists.
        """
        try:
            safe_file = resolve_safe_path(docs_path, file_path)
        except PermissionError as exc:
            return {"success": False, "error": str(exc)}

        if safe_file.exists() and not overwrite:
            return {
                "success": False,
                "error": f"File '{file_path}' already exists and overwrite is set to False.",
            }

        try:
            safe_file.parent.mkdir(parents=True, exist_ok=True)
            norm_content = normalize_newlines(content)
            safe_file.write_text(norm_content, encoding="utf-8")
        except OSError as exc:
            return {"success": False, "error": f"Could not create file: {exc}"}

        return {
            "success": True,
            "file_path": safe_file.relative_to(docs_path).as_posix(),
            "size_bytes": len(norm_content.encode("utf-8")),
        }

    return server
