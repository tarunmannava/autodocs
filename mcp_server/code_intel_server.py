from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from change_graph.ast_parser import ast_parser
from repo_manager.diff import ChangedFile, parse_unified_diff


def create_code_intel_server(
    repo_dir: Path | str,
    raw_diff: Optional[str] = None,
    server_name: str = "code-intelligence",
) -> FastMCP:
    """
    Creates and configures a Code Intelligence FastMCP server bound to the specified repository directory.

    Args:
        repo_dir: Path to the root of the source code repository.
        raw_diff: Optional unified git diff text for the repository.
        server_name: MCP server name identifier.

    Returns:
        FastMCP: Configured server instance with code intelligence tools.
    """
    repo_path = Path(repo_dir).resolve()
    ast_data: Dict[str, Any] = ast_parser.parse_directory(repo_path)
    parsed_diff: List[ChangedFile] = parse_unified_diff(raw_diff) if raw_diff else []

    server = FastMCP(server_name)

    @server.tool()
    def get_diff_summary() -> Dict[str, Any]:
        """
        Returns a structured summary of all modified, added, and deleted files and their diff hunks.
        """
        return {
            "total_files_changed": len(parsed_diff),
            "files": [f.to_dict() for f in parsed_diff],
        }

    @server.tool()
    def get_impacted_symbols(file_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Intersects the PR diff line ranges with AST symbols to find modified functions,
        methods, classes, and their immediate downstream callers.

        Args:
            file_path: Optional filter to restrict analysis to a single file path.
        """
        impacted_symbols: List[Dict[str, Any]] = []
        files_dict = {f["path"]: f for f in ast_data.get("files", [])}

        call_graph = ast_data.get("call_graph", [])

        for changed_file in parsed_diff:
            # Normalize path comparison (handle forward/backward slashes)
            norm_changed_path = changed_file.path.replace("\\", "/")
            if file_path and file_path.replace("\\", "/") != norm_changed_path:
                continue

            matching_file_data = None
            for stored_path, data in files_dict.items():
                if stored_path.replace("\\", "/") == norm_changed_path or norm_changed_path.endswith(
                    stored_path.replace("\\", "/")
                ):
                    matching_file_data = data
                    break

            if not matching_file_data:
                continue

            # Check each hunk against functions, methods, and classes
            all_callables: List[Dict[str, Any]] = []
            for fn in matching_file_data.get("functions", []):
                all_callables.append(dict(fn, parent_class=None))

            for cls in matching_file_data.get("classes", []):
                for method in cls.get("methods", []):
                    all_callables.append(dict(method, parent_class=cls.get("name")))

            for hunk in changed_file.hunks:
                hunk_start = hunk.target_start
                hunk_end = hunk.target_start + max(1, hunk.target_length) - 1

                for item in all_callables:
                    sym_start = item.get("start_line", 1)
                    sym_end = item.get("end_line", sym_start)

                    # Check for line range overlap
                    if not (hunk_end < sym_start or hunk_start > sym_end):
                        symbol_name = item.get("name", "")
                        parent_class = item.get("parent_class")
                        qual_name = item.get("qualified_name") or (
                            f"{parent_class}.{symbol_name}" if parent_class else symbol_name
                        )

                        # Check if overlap is solely within docstring lines
                        docstring = item.get("docstring")
                        is_docstring_only = False
                        if docstring:
                            changed_lines = [
                                line[1:].strip()
                                for line in hunk.patch_text.splitlines()
                                if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
                            ]
                            clean_doc = docstring.strip()
                            if changed_lines and all(
                                not any(kw in line for kw in ("def ", "class ", "return ", "yield ", "async def ", "="))
                                and (
                                    '"""' in line
                                    or "'''" in line
                                    or any(word in clean_doc for word in line.strip("\"'# ").split() if len(word) > 3)
                                )
                                for line in changed_lines
                            ):
                                is_docstring_only = True

                        # Find downstream callers from call graph
                        downstream_callers = []
                        for edge in call_graph:
                            target = edge.get("target", "")
                            call_name = edge.get("call_name", "")
                            if target == qual_name or target.endswith(f".{symbol_name}") or call_name == symbol_name:
                                caller = edge.get("source", "")
                                if caller and caller not in downstream_callers:
                                    downstream_callers.append(caller)

                        route_meta = item.get("route")

                        symbol_entry = {
                            "file_path": changed_file.path,
                            "symbol_name": symbol_name,
                            "qualified_name": qual_name,
                            "parent_class": parent_class,
                            "kind": "method" if parent_class else "function",
                            "start_line": sym_start,
                            "end_line": sym_end,
                            "signature": item.get("signature", ""),
                            "is_docstring_only": is_docstring_only,
                            "route": route_meta,
                            "downstream_callers": downstream_callers,
                        }

                        # Avoid duplicate entries for the same symbol
                        if not any(
                            s["file_path"] == symbol_entry["file_path"]
                            and s["qualified_name"] == symbol_entry["qualified_name"]
                            for s in impacted_symbols
                        ):
                            impacted_symbols.append(symbol_entry)

        return {"impacted_symbols": impacted_symbols}

    @server.tool()
    def get_symbol_info(file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Retrieves detailed information about a specific function, method, or class
        including its parameters, signature, return type, docstring, and decorators.

        Args:
            file_path: Source file path relative to the repository root.
            symbol_name: Name of the symbol (supports class.method notation).
        """
        norm_query_path = file_path.replace("\\", "/")
        target_file = None

        for f in ast_data.get("files", []):
            f_path = f["path"].replace("\\", "/")
            if f_path == norm_query_path or norm_query_path.endswith(f_path):
                target_file = f
                break

        if not target_file:
            return {"error": f"File not found in AST index: '{file_path}'"}

        parts = symbol_name.split(".")
        if len(parts) == 2:
            target_class_name, target_method_name = parts
            for cls in target_file.get("classes", []):
                if cls.get("name") == target_class_name:
                    for method in cls.get("methods", []):
                        if method.get("name") == target_method_name:
                            return {
                                "file_path": file_path,
                                "symbol_name": symbol_name,
                                "kind": "method",
                                "parent_class": target_class_name,
                                "start_line": method.get("start_line"),
                                "end_line": method.get("end_line"),
                                "signature": method.get("signature"),
                                "parameters": method.get("parameters"),
                                "return_annotation": method.get("return_annotation"),
                                "docstring": method.get("docstring"),
                                "decorators": method.get("decorators"),
                            }
        else:
            # Check functions
            for fn in target_file.get("functions", []):
                if fn.get("name") == symbol_name:
                    return {
                        "file_path": file_path,
                        "symbol_name": symbol_name,
                        "kind": "function",
                        "start_line": fn.get("start_line"),
                        "end_line": fn.get("end_line"),
                        "signature": fn.get("signature"),
                        "parameters": fn.get("parameters"),
                        "return_annotation": fn.get("return_annotation"),
                        "docstring": fn.get("docstring"),
                        "decorators": fn.get("decorators"),
                        "route": fn.get("route"),
                    }
            # Check classes
            for cls in target_file.get("classes", []):
                if cls.get("name") == symbol_name:
                    return {
                        "file_path": file_path,
                        "symbol_name": symbol_name,
                        "kind": "class",
                        "start_line": cls.get("start_line"),
                        "end_line": cls.get("end_line"),
                        "bases": cls.get("bases"),
                        "methods": [m.get("name") for m in cls.get("methods", [])],
                        "docstring": cls.get("docstring"),
                        "decorators": cls.get("decorators"),
                    }

        return {"error": f"Symbol '{symbol_name}' not found in '{file_path}'"}

    @server.tool()
    def get_call_graph(symbol_name: str, depth: int = 1) -> Dict[str, Any]:
        """
        Queries the repository call graph for upstream callers and downstream callees of a symbol.

        Args:
            symbol_name: The function or method name to query.
            depth: Graph search depth (default 1).
        """
        call_graph = ast_data.get("call_graph", [])
        callers = []
        callees = []

        for edge in call_graph:
            source = edge.get("source", "")
            target = edge.get("target", "")
            call_name = edge.get("call_name", "")

            # If target matches, source is a caller
            if target == symbol_name or target.endswith(f".{symbol_name}") or call_name == symbol_name:
                callers.append(
                    {
                        "caller": source,
                        "target": target,
                        "resolution": edge.get("resolution"),
                        "line": edge.get("line"),
                    }
                )

            # If source matches, target is a callee
            if source == symbol_name or source.endswith(f".{symbol_name}"):
                callees.append(
                    {
                        "callee": target,
                        "call_name": call_name,
                        "resolution": edge.get("resolution"),
                        "line": edge.get("line"),
                    }
                )

        return {
            "symbol": symbol_name,
            "callers_count": len(callers),
            "callees_count": len(callees),
            "callers": callers,
            "callees": callees,
        }

    return server
