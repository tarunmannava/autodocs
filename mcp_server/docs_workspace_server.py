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
    google_docs_id: Optional[str] = None,
    google_credentials_path: Optional[str] = None,
) -> FastMCP:
    """
    Creates and configures a Docs Workspace FastMCP server bound to the target documentation directory.
    Optionally provides Google Docs synchronization tools when a Google Document ID is configured.

    Args:
        docs_dir: Path to the root of the target documentation repository.
        server_name: MCP server name identifier.
        google_docs_id: Optional Google Document ID for cloud documentation integration.
        google_credentials_path: Optional path to Google credentials JSON.

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
            # Fallback: whitespace-tolerant line-by-line match (ignoring trailing whitespace)
            text_lines = norm_text.splitlines()
            target_lines = [line.rstrip() for line in norm_target.splitlines()]
            target_len = len(target_lines)

            matching_indices: List[int] = []
            if target_len > 0 and len(text_lines) >= target_len:
                for i in range(len(text_lines) - target_len + 1):
                    window = [line.rstrip() for line in text_lines[i : i + target_len]]
                    if window == target_lines:
                        matching_indices.append(i)

            if len(matching_indices) == 1:
                start_i = matching_indices[0]
                replacement_lines = norm_replacement.splitlines()
                updated_lines = text_lines[:start_i] + replacement_lines + text_lines[start_i + target_len :]
                updated_text = "\n".join(updated_lines)
                if norm_text.endswith("\n"):
                    updated_text += "\n"
            elif len(matching_indices) > 1:
                count = len(matching_indices)
                return {
                    "success": False,
                    "error": (
                        f"target_block matched {count} sections with whitespace-tolerant matching in '{file_path}'. "
                        "Provide more surrounding context lines to uniquely identify the section."
                    ),
                }
            else:
                return {
                    "success": False,
                    "error": f"target_block not found in '{file_path}'. Ensure exact character and whitespace match.",
                }
        elif match_count > 1:
            return {
                "success": False,
                "error": (
                    f"target_block found {match_count} times in '{file_path}'. "
                    "Provide more surrounding context lines to uniquely identify the section."
                ),
            }
        else:
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

    # Register Google Docs tools if a Google Document ID is configured or present in environment
    effective_gdoc_id = google_docs_id or os.getenv("AUTODOCS_GOOGLE_DOCS_DOCUMENT_ID")
    if effective_gdoc_id:
        _cached_gdoc_service: Optional[Any] = None

        def _get_service() -> Any:
            nonlocal _cached_gdoc_service
            if _cached_gdoc_service is None:
                from backend.services.google_docs import GoogleDocsService

                _cached_gdoc_service = GoogleDocsService(credentials_path=google_credentials_path)
            return _cached_gdoc_service

        @server.tool()
        def read_google_doc(document_id: Optional[str] = None) -> Dict[str, Any]:
            """
            Reads the current plain text content and metadata of the configured Google Doc.

            Args:
                document_id: Optional document ID override (defaults to the configured Google Doc ID).
            """
            target_id = document_id or effective_gdoc_id
            try:
                service = _get_service()
                doc = service.get_document(target_id)
                title = doc.get("title", "")
                elements = doc.get("body", {}).get("content", [])
                text_parts = []
                for elem in elements:
                    p = elem.get("paragraph")
                    if p:
                        for pe in p.get("elements", []):
                            text_parts.append(pe.get("textRun", {}).get("content", ""))
                return {
                    "status": "success",
                    "document_id": target_id,
                    "title": title,
                    "url": f"https://docs.google.com/document/d/{target_id}/edit",
                    "content": "".join(text_parts),
                }
            except Exception as exc:
                return {"status": "error", "error": str(exc), "document_id": target_id}

        @server.tool()
        def edit_google_doc(
            target_block: str,
            replacement_block: str,
            document_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            """
            Applies a precise replacement edit to an existing Google Doc in-place,
            preserving surrounding content (analogous to edit_doc_file).
            Use this when existing code functionality changes (e.g., adding filters or updating parameters)
            to replace specific lines with the updated lines.

            Args:
                target_block: Exact text block currently in the Google Doc to be replaced.
                replacement_block: Replacement Markdown text (can include bold, code, headings, bullets).
                document_id: Optional document ID override (defaults to configured Google Doc).
            """
            target_id = document_id or effective_gdoc_id
            try:
                service = _get_service()
                result = service.replace_text_block(target_id, target_block, replacement_block)
                return result
            except Exception as exc:
                return {"success": False, "error": str(exc), "document_id": target_id}

        @server.tool()
        def insert_into_google_doc(
            anchor_text: str,
            content: str,
            position: str = "after",
            document_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            """
            Inserts new documentation lines directly before or after an anchor text line in the Google Doc in-place.
            Preserves all surrounding text and formatting.

            Args:
                anchor_text: Exact text line in the Google Doc to anchor to (e.g. parameter or heading).
                content: Markdown text to insert (e.g. new query filter line, bullet item, or note).
                position: Either 'after' (insert directly below anchor) or 'before' (insert directly above anchor).
                document_id: Optional document ID override.
            """
            target_id = document_id or effective_gdoc_id
            try:
                service = _get_service()
                result = service.insert_at_anchor(target_id, anchor_text, content, position=position)
                return result
            except Exception as exc:
                return {"success": False, "error": str(exc), "document_id": target_id}

        @server.tool()
        def update_google_doc_section(
            heading_title: str,
            markdown_content: str,
            document_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            """
            Updates or creates a specific section under a given heading in the Google Doc (e.g. 'Tasks CRUD'),
            leaving all other sections and headings completely intact.

            Args:
                heading_title: Title of the heading (e.g. 'API Endpoints' or 'Tasks CRUD').
                markdown_content: Updated Markdown content for that specific section.
                document_id: Optional document ID override.
            """
            target_id = document_id or effective_gdoc_id
            try:
                service = _get_service()
                result = service.patch_section(target_id, heading_title, markdown_content)
                return result
            except Exception as exc:
                return {"success": False, "error": str(exc), "document_id": target_id}

        @server.tool()
        def update_google_doc(
            markdown_content: str,
            document_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            """
            Translates Markdown content into native Google Docs rich text (real headings,
            real native tables, bold text, Consolas monospace code blocks) and updates the Google Doc non-destructively.

            Args:
                markdown_content: Formatted Markdown text to synchronize into the Google Doc.
                document_id: Optional document ID override (defaults to the configured Google Doc ID).
            """
            target_id = document_id or effective_gdoc_id
            try:
                service = _get_service()
                doc_url = service.sync_markdown_to_doc(target_id, markdown_content, clear_first=False)
                return {
                    "status": "success",
                    "document_id": target_id,
                    "url": doc_url,
                    "message": "Successfully synchronized rich documentation to Google Docs.",
                }
            except Exception as exc:
                return {"status": "error", "error": str(exc), "document_id": target_id}

    return server
