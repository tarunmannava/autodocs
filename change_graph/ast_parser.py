from __future__ import annotations

import ast
import hashlib
import io
import json
import tokenize
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

DEFAULT_TEMP_REPOS_DIR = Path("backend/temp_repos")


def get_source_directory(project_id: str) -> Path:
    return DEFAULT_TEMP_REPOS_DIR / project_id / "source"


def get_ast_path(project_id: str) -> Path:
    return DEFAULT_TEMP_REPOS_DIR / project_id / "ast.json"


PYTHON_SUFFIXES: Set[str] = {".py", ".pyi"}

TEXT_LANGUAGE_BY_SUFFIX: Dict[str, str] = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ps1": "powershell",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "configuration",
    ".conf": "configuration",
    ".md": "markdown",
    ".rst": "restructuredtext",
    ".txt": "text",
    ".xml": "xml",
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".gql": "graphql",
}

SPECIAL_TEXT_FILENAMES: Dict[str, str] = {
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "procfile": "procfile",
    "requirements.txt": "requirements",
    "pyproject.toml": "toml",
    "package.json": "json",
    "package-lock.json": "json",
    "pnpm-lock.yaml": "yaml",
    "yarn.lock": "text",
    "readme": "markdown",
    "readme.md": "markdown",
    "license": "text",
    "license.md": "markdown",
}

DEFAULT_IGNORED_DIRECTORIES: Set[str] = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "node_modules",
    ".next",
    ".nuxt",
    "dist",
    "build",
    "target",
    "coverage",
    "vendor",
    "generated_docs",
    "vector_store",
    "chroma_db",
}

SENSITIVE_FILENAMES: Set[str] = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    "id_rsa",
    "id_ed25519",
}

SENSITIVE_SUFFIXES: Set[str] = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
}


@dataclass(frozen=True)
class ParserConfig:
    """Configuration for safe, incremental project parsing."""

    include_non_python_files: bool = True
    reuse_unchanged_files: bool = True
    follow_symlinks: bool = False
    max_file_bytes: int = 2_000_000
    max_expression_characters: int = 1_500
    max_docstring_characters: int = 20_000
    ignored_directories: Tuple[str, ...] = tuple(sorted(DEFAULT_IGNORED_DIRECTORIES))

    def validate(self) -> None:
        if self.max_file_bytes < 1_024:
            raise ValueError("max_file_bytes must be at least 1024 bytes.")
        if self.max_expression_characters < 100:
            raise ValueError("max_expression_characters must be at least 100.")
        if self.max_docstring_characters < 100:
            raise ValueError("max_docstring_characters must be at least 100.")


DEFAULT_PARSER_CONFIG = ParserConfig()


class DirectCallableVisitor(ast.NodeVisitor):
    """
    Collect behavior belonging directly to one callable.

    Nested functions, nested classes, and lambdas are not traversed, so their
    calls and control-flow metrics are not incorrectly assigned to the parent.
    """

    def __init__(self) -> None:
        self.calls: List[ast.Call] = []
        self.returns: List[ast.Return] = []
        self.raises: List[ast.Raise] = []
        self.conditions: List[ast.If] = []
        self.loops: List[Union[ast.For, ast.AsyncFor, ast.While]] = []
        self.try_blocks: List[ast.Try] = []
        self.with_blocks: List[Union[ast.With, ast.AsyncWith]] = []
        self.match_blocks: List[ast.Match] = []
        self.asserts: List[ast.Assert] = []
        self.awaits: List[ast.Await] = []
        self.yields: List[Union[ast.Yield, ast.YieldFrom]] = []
        self.comprehensions: List[ast.comprehension] = []
        self.bool_operations: List[ast.BoolOp] = []
        self.breaks: List[ast.Break] = []
        self.continues: List[ast.Continue] = []

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self.returns.append(node)
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self.raises.append(node)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.conditions.append(node)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.loops.append(node)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.loops.append(node)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.loops.append(node)
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self.try_blocks.append(node)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self.with_blocks.append(node)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.with_blocks.append(node)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.match_blocks.append(node)
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.asserts.append(node)
        self.generic_visit(node)

    def visit_Await(self, node: ast.Await) -> None:
        self.awaits.append(node)
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        self.yields.append(node)
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self.yields.append(node)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.comprehensions.append(node)
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.bool_operations.append(node)
        self.generic_visit(node)

    def visit_Break(self, node: ast.Break) -> None:
        self.breaks.append(node)

    def visit_Continue(self, node: ast.Continue) -> None:
        self.continues.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


class DirectInstanceAttributeVisitor(ast.NodeVisitor):
    """Collect direct `self.x` assignments while skipping nested scopes."""

    def __init__(self, owner_names: Set[str]) -> None:
        self.owner_names = owner_names
        self.attributes: List[Dict[str, Any]] = []
        self._seen: Set[Tuple[str, int]] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        value = ASTParser.safe_unparse_static(node.value)
        for target in node.targets:
            self._capture_target(target, value=value, annotation=None, line=node.lineno)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        value = ASTParser.safe_unparse_static(node.value)
        annotation = ASTParser.safe_unparse_static(node.annotation)
        self._capture_target(
            node.target,
            value=value,
            annotation=annotation,
            line=node.lineno,
        )
        if node.value is not None:
            self.generic_visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        value = ASTParser.safe_unparse_static(node.value)
        self._capture_target(node.target, value=value, annotation=None, line=node.lineno)
        self.generic_visit(node.value)

    def _capture_target(
        self,
        target: ast.AST,
        *,
        value: Optional[str],
        annotation: Optional[str],
        line: int,
    ) -> None:
        if not isinstance(target, ast.Attribute):
            return
        if not isinstance(target.value, ast.Name):
            return
        if target.value.id not in self.owner_names:
            return

        key = (target.attr, line)
        if key in self._seen:
            return
        self._seen.add(key)
        self.attributes.append(
            {
                "name": target.attr,
                "value": value,
                "annotation": annotation,
                "owner": target.value.id,
                "line": line,
            }
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


class ImportCollector(ast.NodeVisitor):
    """Collect structured imports and the scope where each import appears."""

    def __init__(self, module_name: str) -> None:
        self.module_name = module_name
        self.scope_stack: List[str] = [module_name or "<module>"]
        self.imports: List[Dict[str, Any]] = []

    @property
    def scope(self) -> str:
        return self.scope_stack[-1]

    def visit_Import(self, node: ast.Import) -> None:
        aliases = [
            {"name": alias.name, "asname": alias.asname}
            for alias in node.names
        ]
        self.imports.append(
            {
                "kind": "import",
                "module": None,
                "level": 0,
                "names": aliases,
                "scope": self.scope,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
            }
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        aliases = [
            {"name": alias.name, "asname": alias.asname}
            for alias in node.names
        ]
        self.imports.append(
            {
                "kind": "from",
                "module": node.module,
                "level": node.level,
                "names": aliases,
                "scope": self.scope,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
            }
        )

    def _visit_function(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> None:
        self.scope_stack.append(f"{self.scope}.{node.name}")
        for statement in node.body:
            self.visit(statement)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope_stack.append(f"{self.scope}.{node.name}")
        for statement in node.body:
            self.visit(statement)
        self.scope_stack.pop()


class NestingDepthVisitor(ast.NodeVisitor):
    """Estimate maximum direct-scope control-flow nesting depth."""

    NESTING_NODES = tuple(
        node_type
        for node_type in (
            ast.If,
            ast.For,
            ast.AsyncFor,
            ast.While,
            ast.Try,
            ast.With,
            ast.AsyncWith,
            getattr(ast, "Match", None),
            getattr(ast, "TryStar", None),
        )
        if node_type is not None
    )

    def __init__(self) -> None:
        self.current_depth = 0
        self.max_depth = 0

    def generic_visit(self, node: ast.AST) -> None:
        is_nesting = isinstance(node, self.NESTING_NODES)
        if is_nesting:
            self.current_depth += 1
            self.max_depth = max(self.max_depth, self.current_depth)
        super().generic_visit(node)
        if is_nesting:
            self.current_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


class ASTParser:
    """
    High-fidelity Python project parser with backward-compatible output.

    Existing consumers can continue using:

        ast_parser.parse_project(project_id)

    Existing fields such as `files`, `imports`, `classes`, `functions`,
    `methods`, `start_line`, `end_line`, `code`, and `dependencies` remain.
    New fields improve retrieval, documentation, graph construction, and
    incremental indexing.
    """

    SCHEMA_VERSION = "3.0"
    PARSER_NAME = "python_ast_parser"

    def __init__(self, config: ParserConfig = DEFAULT_PARSER_CONFIG) -> None:
        config.validate()
        self.config = config

    def parse_project(self, project_id: str) -> Dict[str, Any]:
        source_directory = get_source_directory(project_id)
        output_path = get_ast_path(project_id)
        return self.parse_directory(
            directory_path=source_directory,
            project_id=project_id,
            save_output=True,
            output_path=output_path,
        )

    def parse_directory(
        self,
        directory_path: Union[str, Path],
        *,
        project_id: Optional[str] = None,
        save_output: bool = False,
        output_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        source_directory = Path(directory_path)

        if not source_directory.exists():
            raise FileNotFoundError(f"Source directory not found: {source_directory}")
        if not source_directory.is_dir():
            raise ValueError(f"Source path is not a directory: {source_directory}")

        source_directory = source_directory.resolve()
        effective_project_id = project_id or source_directory.name

        previous_ast: Dict[str, Any] = {}
        if output_path is not None:
            previous_ast = self._load_previous_ast(ast_path=Path(output_path))
        elif project_id is not None:
            previous_ast = self._load_previous_ast(project_id=project_id)

        previous_files = {
            item.get("path"): item
            for item in previous_ast.get("files", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        can_reuse_previous = self._can_reuse_previous_project(previous_ast)

        candidates, skipped_files = self._discover_project_files(source_directory)
        language_counts = Counter(self._detect_language(path) for path in candidates)

        project_result: Dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "parser": self.PARSER_NAME,
            "parser_config_signature": self._config_signature(),
            "project_id": effective_project_id,
            "language": "python",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "root": str(source_directory),
            "total_file_count": len(candidates),
            "total_python_file_count": sum(
                1 for path in candidates if path.suffix.lower() in PYTHON_SUFFIXES
            ),
            "file_count": 0,
            "error_count": 0,
            "skipped_count": len(skipped_files),
            "reused_file_count": 0,
            "language_counts": dict(sorted(language_counts.items())),
            "files": [],
            "dependencies": [],
            "external_dependencies": [],
            "unresolved_imports": [],
            "call_graph": [],
            "inheritance_graph": [],
            "routes": [],
            "symbols": [],
            "skipped_files": skipped_files,
        }

        for file_path in candidates:
            relative_path = file_path.relative_to(source_directory)
            relative_path_string = relative_path.as_posix()
            language = self._detect_language(file_path)

            try:
                if language == "python":
                    source_code, encoding = self._read_python_source(file_path)
                else:
                    source_code, encoding = self._read_text_source(file_path)

                content_hash = self._generate_hash(source_code)
                previous_file = previous_files.get(relative_path_string)

                if (
                    can_reuse_previous
                    and self.config.reuse_unchanged_files
                    and isinstance(previous_file, dict)
                    and previous_file.get("content_hash") == content_hash
                    and previous_file.get("status") in {"parsed", "unparsed"}
                ):
                    reused_file = dict(previous_file)
                    reused_file["reused"] = True
                    project_result["files"].append(reused_file)
                    project_result["reused_file_count"] += 1
                    continue

                if language == "python":
                    parsed_file = self.parse_file(
                        project_id=effective_project_id,
                        file_path=file_path,
                        root=source_directory,
                        source_code=source_code,
                        encoding=encoding,
                    )
                else:
                    parsed_file = self._build_unparsed_text_entry(
                        project_id=effective_project_id,
                        file_path=file_path,
                        root=source_directory,
                        source_code=source_code,
                        encoding=encoding,
                        language=language,
                    )

                project_result["files"].append(parsed_file)

            except SyntaxError as error:
                project_result["files"].append(
                    self._build_error_entry(
                        project_id=effective_project_id,
                        relative_path=relative_path,
                        error=error,
                    )
                )
            except Exception as error:
                project_result["files"].append(
                    self._build_error_entry(
                        project_id=effective_project_id,
                        relative_path=relative_path,
                        error=error,
                    )
                )

        project_result["file_count"] = sum(
            1 for item in project_result["files"] if item.get("status") == "parsed"
        )
        project_result["error_count"] = sum(
            1 for item in project_result["files"] if item.get("status") == "error"
        )

        dependency_result = self._build_dependencies(project_result["files"])
        project_result["dependencies"] = dependency_result["internal"]
        project_result["external_dependencies"] = dependency_result["external"]
        project_result["unresolved_imports"] = dependency_result["unresolved"]
        project_result["symbols"] = self._build_project_symbol_index(project_result["files"])
        project_result["call_graph"] = self._build_call_graph(project_result["files"])
        project_result["inheritance_graph"] = self._build_inheritance_graph(
            project_result["files"]
        )
        project_result["routes"] = self._collect_project_routes(project_result["files"])
        project_result["statistics"] = self._build_project_statistics(project_result)

        if save_output:
            if output_path is not None:
                self.save_ast(data=project_result, output_path=Path(output_path))
            elif project_id is not None:
                self.save_ast(project_id=project_id, data=project_result)
            else:
                self.save_ast(data=project_result, output_path=source_directory / "ast.json")

        return project_result

    def parse_file(
        self,
        project_id: str,
        file_path: Path,
        root: Path,
        source_code: Optional[str] = None,
        encoding: Optional[str] = None,
    ) -> Dict[str, Any]:
        relative_path = file_path.relative_to(root)
        relative_path_string = relative_path.as_posix()
        module_name = self._get_module_name(relative_path)
        is_package = file_path.name in {"__init__.py", "__init__.pyi"}

        if source_code is None:
            source_code, detected_encoding = self._read_python_source(file_path)
            encoding = encoding or detected_encoding

        tree = ast.parse(
            source_code,
            filename=relative_path_string,
            type_comments=True,
        )

        import_details = self.extract_import_details(tree=tree, module_name=module_name)
        imports = self._flatten_import_details(import_details)
        classes = self.extract_classes(
            project_id=project_id,
            tree=tree,
            module_name=module_name,
            file_path=relative_path_string,
            source_code=source_code,
        )
        functions = self.extract_functions(
            project_id=project_id,
            tree=tree,
            module_name=module_name,
            file_path=relative_path_string,
            source_code=source_code,
        )

        file_result: Dict[str, Any] = {
            "id": self._generate_hash(f"{project_id}:{relative_path_string}"),
            "path": relative_path_string,
            "module_name": module_name,
            "package_name": self._get_package_name(module_name, is_package),
            "is_package": is_package,
            "status": "parsed",
            "language": "python",
            "encoding": encoding or "utf-8",
            "file_size_bytes": file_path.stat().st_size,
            "content_hash": self._generate_hash(source_code),
            "source_line_count": len(source_code.splitlines()),
            "logical_line_count": self._count_logical_python_lines(source_code),
            "module_docstring": self._truncate_docstring(ast.get_docstring(tree, clean=False)),
            "module_docstring_clean": self._truncate_docstring(ast.get_docstring(tree, clean=True)),
            "future_imports": self._extract_future_imports(tree),
            "imports": imports,
            "import_details": import_details,
            "module_variables": self.extract_module_variables(tree),
            "classes": classes,
            "functions": functions,
            "comments": self.extract_comments(source_code),
            "type_aliases": self._extract_type_aliases(tree),
            "all_exports": self._extract_all_exports(tree),
            "main_guard": self._extract_main_guard(tree),
            "routes": self._extract_routes_from_symbols(classes, functions),
            "metrics": self._build_file_metrics(tree, source_code, classes, functions),
            "reused": False,
        }
        file_result["symbols"] = self._flatten_file_symbols(file_result)
        return file_result

    def extract_import_details(self, tree: ast.AST, module_name: str) -> List[Dict[str, Any]]:
        collector = ImportCollector(module_name)
        collector.visit(tree)
        return collector.imports

    def extract_imports(self, tree: ast.AST) -> List[str]:
        """Backward-compatible import string list."""
        return self._flatten_import_details(self.extract_import_details(tree, ""))

    def extract_module_variables(self, tree: ast.Module) -> List[Dict[str, Any]]:
        variables: List[Dict[str, Any]] = []
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                variables.extend(self._extract_assignment_records(node, scope="module"))
        return variables

    def extract_classes(
        self,
        project_id: str,
        tree: ast.Module,
        module_name: str,
        file_path: str,
        source_code: str,
    ) -> List[Dict[str, Any]]:
        classes: List[Dict[str, Any]] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                qualified_name = f"{module_name}.{node.name}" if module_name else node.name
                classes.append(
                    self._extract_class(
                        project_id=project_id,
                        node=node,
                        qualified_name=qualified_name,
                        parent_qualified_name=module_name,
                        file_path=file_path,
                        source_code=source_code,
                    )
                )
        return classes

    def _extract_class(
        self,
        *,
        project_id: str,
        node: ast.ClassDef,
        qualified_name: str,
        parent_qualified_name: str,
        file_path: str,
        source_code: str,
    ) -> Dict[str, Any]:
        start_line = self._decorated_start_line(node)
        class_code = self._extract_source_code(source_code, node, start_line=start_line)

        methods: List[Dict[str, Any]] = []
        nested_classes: List[Dict[str, Any]] = []
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append(
                    self.extract_callable(
                        project_id=project_id,
                        node=child,
                        qualified_name=f"{qualified_name}.{child.name}",
                        parent_qualified_name=qualified_name,
                        file_path=file_path,
                        source_code=source_code,
                        callable_kind=self._get_method_kind(child),
                    )
                )
            elif isinstance(child, ast.ClassDef):
                nested_classes.append(
                    self._extract_class(
                        project_id=project_id,
                        node=child,
                        qualified_name=f"{qualified_name}.{child.name}",
                        parent_qualified_name=qualified_name,
                        file_path=file_path,
                        source_code=source_code,
                    )
                )

        return {
            "id": self._generate_hash(f"{project_id}:{file_path}:{qualified_name}"),
            "content_hash": self._generate_hash(class_code),
            "node_type": "ClassDef",
            "symbol_type": "class",
            "name": node.name,
            "qualified_name": qualified_name,
            "parent_qualified_name": parent_qualified_name,
            "docstring": self._truncate_docstring(ast.get_docstring(node, clean=False)),
            "docstring_clean": self._truncate_docstring(ast.get_docstring(node, clean=True)),
            "bases": [self.get_name(base) for base in node.bases],
            "keywords": {
                keyword.arg or "**": self.get_name(keyword.value)
                for keyword in node.keywords
            },
            "decorators": [self.get_name(item) for item in node.decorator_list],
            "definition_line": node.lineno,
            "start_line": start_line,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "start_column": node.col_offset,
            "end_column": getattr(node, "end_col_offset", node.col_offset),
            "code": class_code,
            "class_attributes": self._extract_class_attributes(node),
            "instance_attributes": self._extract_instance_attributes(node),
            "methods": methods,
            "nested_classes": nested_classes,
            "protocols": self._infer_class_protocols(node),
            "metrics": {
                "method_count": len(methods),
                "nested_class_count": len(nested_classes),
                "line_count": max(1, getattr(node, "end_lineno", node.lineno) - start_line + 1),
            },
        }

    def extract_functions(
        self,
        project_id: str,
        tree: ast.Module,
        module_name: str,
        file_path: str,
        source_code: str,
    ) -> List[Dict[str, Any]]:
        functions: List[Dict[str, Any]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified_name = f"{module_name}.{node.name}" if module_name else node.name
                functions.append(
                    self.extract_callable(
                        project_id=project_id,
                        node=node,
                        qualified_name=qualified_name,
                        parent_qualified_name=module_name,
                        file_path=file_path,
                        source_code=source_code,
                        callable_kind="function",
                    )
                )
        return functions

    def extract_callable(
        self,
        project_id: str,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
        qualified_name: str,
        parent_qualified_name: str,
        file_path: str,
        source_code: str,
        callable_kind: str,
    ) -> Dict[str, Any]:
        start_line = self._decorated_start_line(node)
        function_code = self._extract_source_code(source_code, node, start_line=start_line)

        visitor = DirectCallableVisitor()
        depth_visitor = NestingDepthVisitor()
        for statement in node.body:
            visitor.visit(statement)
            depth_visitor.visit(statement)

        nested_functions: List[Dict[str, Any]] = []
        nested_classes: List[Dict[str, Any]] = []
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested_functions.append(
                    self.extract_callable(
                        project_id=project_id,
                        node=child,
                        qualified_name=f"{qualified_name}.{child.name}",
                        parent_qualified_name=qualified_name,
                        file_path=file_path,
                        source_code=source_code,
                        callable_kind="nested_function",
                    )
                )
            elif isinstance(child, ast.ClassDef):
                nested_classes.append(
                    self._extract_class(
                        project_id=project_id,
                        node=child,
                        qualified_name=f"{qualified_name}.{child.name}",
                        parent_qualified_name=qualified_name,
                        file_path=file_path,
                        source_code=source_code,
                    )
                )

        calls = [self._extract_call(call) for call in visitor.calls]
        returns = [
            {"expression": self.get_name(item.value), "line": item.lineno}
            for item in visitor.returns
        ]
        raises = [
            {
                "exception": self._extract_raise_type(item),
                "message": self._extract_raise_message(item),
                "line": item.lineno,
            }
            for item in visitor.raises
        ]
        conditions = [
            {"type": "if", "expression": self.get_name(item.test), "line": item.lineno}
            for item in visitor.conditions
        ]
        loops = [self._extract_loop(item) for item in visitor.loops]
        handled_exceptions = self._extract_handled_exceptions(visitor.try_blocks)
        decorators = [self.get_name(item) for item in node.decorator_list]
        parameters = self._extract_parameters(node)
        route_metadata = self._extract_route_metadata(node)
        decision_points = (
            len(visitor.conditions)
            + len(visitor.loops)
            + sum(len(item.handlers) for item in visitor.try_blocks)
            + sum(max(0, len(item.values) - 1) for item in visitor.bool_operations)
            + sum(len(item.cases) for item in visitor.match_blocks)
            + len(visitor.comprehensions)
        )

        return {
            "id": self._generate_hash(f"{project_id}:{file_path}:{qualified_name}"),
            "content_hash": self._generate_hash(function_code),
            "node_type": "AsyncFunctionDef" if isinstance(node, ast.AsyncFunctionDef) else "FunctionDef",
            "symbol_type": "callable",
            "callable_kind": callable_kind,
            "name": node.name,
            "qualified_name": qualified_name,
            "parent_qualified_name": parent_qualified_name,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "is_generator": bool(visitor.yields),
            "is_coroutine_generator": isinstance(node, ast.AsyncFunctionDef) and bool(visitor.yields),
            "parameters": parameters,
            "signature": self._extract_signature(source_code, node),
            "return_annotation": self.get_name(node.returns),
            "type_comment": getattr(node, "type_comment", None),
            "docstring": self._truncate_docstring(ast.get_docstring(node, clean=False)),
            "docstring_clean": self._truncate_docstring(ast.get_docstring(node, clean=True)),
            "decorators": decorators,
            "definition_line": node.lineno,
            "start_line": start_line,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "start_column": node.col_offset,
            "end_column": getattr(node, "end_col_offset", node.col_offset),
            "code": function_code,
            "calls": calls,
            "returns": returns,
            "raises": raises,
            "handled_exceptions": handled_exceptions,
            "conditions": conditions,
            "loops": loops,
            "with_blocks": [self._extract_with_block(item) for item in visitor.with_blocks],
            "match_blocks": [self._extract_match_block(item) for item in visitor.match_blocks],
            "assertions": [
                {"expression": self.get_name(item.test), "message": self.get_name(item.msg), "line": item.lineno}
                for item in visitor.asserts
            ],
            "nested_functions": nested_functions,
            "nested_classes": nested_classes,
            "route": route_metadata,
            "control_flow": {
                "if_count": len(conditions),
                "loop_count": len(loops),
                "try_count": len(visitor.try_blocks),
                "with_count": len(visitor.with_blocks),
                "match_count": len(visitor.match_blocks),
                "return_count": len(returns),
                "raise_count": len(raises),
                "assert_count": len(visitor.asserts),
                "await_count": len(visitor.awaits),
                "yield_count": len(visitor.yields),
                "break_count": len(visitor.breaks),
                "continue_count": len(visitor.continues),
            },
            "metrics": {
                "line_count": max(1, getattr(node, "end_lineno", node.lineno) - start_line + 1),
                "parameter_count": self._count_parameters(parameters),
                "call_count": len(calls),
                "cyclomatic_complexity": 1 + decision_points,
                "max_nesting_depth": depth_visitor.max_depth,
                "nested_function_count": len(nested_functions),
                "nested_class_count": len(nested_classes),
            },
        }

    def extract_comments(self, source_code: str) -> List[Dict[str, Any]]:
        comments: List[Dict[str, Any]] = []
        try:
            tokens = tokenize.generate_tokens(io.StringIO(source_code).readline)
            for token in tokens:
                if token.type == tokenize.COMMENT:
                    comments.append(
                        {
                            "line": token.start[0],
                            "column": token.start[1],
                            "end_line": token.end[0],
                            "end_column": token.end[1],
                            "content": token.string.lstrip("#").strip(),
                            "is_type_comment": token.string.lstrip().startswith("# type:"),
                            "is_noqa": "noqa" in token.string.lower(),
                        }
                    )
        except (tokenize.TokenError, IndentationError):
            return []
        return comments

    def get_name(self, node: Optional[ast.AST]) -> Optional[str]:
        if node is None:
            return None
        text = self.safe_unparse_static(node)
        if text is None:
            return None
        if len(text) > self.config.max_expression_characters:
            return text[: self.config.max_expression_characters] + "..."
        return text

    @staticmethod
    def safe_unparse_static(node: Optional[ast.AST]) -> Optional[str]:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            left = ASTParser.safe_unparse_static(node.value)
            return f"{left}.{node.attr}" if left else node.attr
        if isinstance(node, ast.Constant):
            return repr(node.value)
        try:
            return ast.unparse(node).strip()
        except Exception:
            return type(node).__name__

    def save_ast(
        self,
        project_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        *,
        output_path: Optional[Union[str, Path]] = None,
    ) -> None:
        if data is None:
            raise ValueError("data dictionary must be provided to save_ast")
        if output_path is not None:
            target_path = Path(output_path)
        elif project_id is not None:
            target_path = get_ast_path(project_id)
        else:
            raise ValueError("Either output_path or project_id must be provided to save_ast")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = target_path.with_suffix(target_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary_path.replace(target_path)

 

    def _extract_parameters(
        self,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    ) -> Dict[str, Any]:
        arguments = node.args
        positional_arguments = list(arguments.posonlyargs) + list(arguments.args)
        defaults = list(arguments.defaults)
        default_start_index = len(positional_arguments) - len(defaults)

        positional_parameters: List[Dict[str, Any]] = []
        for index, argument in enumerate(positional_arguments):
            default_node = None
            if index >= default_start_index:
                default_node = defaults[index - default_start_index]
            positional_parameters.append(
                {
                    "name": argument.arg,
                    "annotation": self.get_name(argument.annotation),
                    "default": self.get_name(default_node),
                    "required": default_node is None,
                    "kind": "positional_only" if index < len(arguments.posonlyargs) else "positional_or_keyword",
                }
            )

        keyword_only_parameters: List[Dict[str, Any]] = []
        for argument, default_node in zip(arguments.kwonlyargs, arguments.kw_defaults):
            keyword_only_parameters.append(
                {
                    "name": argument.arg,
                    "annotation": self.get_name(argument.annotation),
                    "default": self.get_name(default_node),
                    "required": default_node is None,
                    "kind": "keyword_only",
                }
            )

        return {
            "positional": positional_parameters,
            "vararg": (
                {
                    "name": arguments.vararg.arg,
                    "annotation": self.get_name(arguments.vararg.annotation),
                    "kind": "var_positional",
                }
                if arguments.vararg
                else None
            ),
            "keyword_only": keyword_only_parameters,
            "kwarg": (
                {
                    "name": arguments.kwarg.arg,
                    "annotation": self.get_name(arguments.kwarg.annotation),
                    "kind": "var_keyword",
                }
                if arguments.kwarg
                else None
            ),
        }

    def _extract_class_attributes(self, class_node: ast.ClassDef) -> List[Dict[str, Any]]:
        attributes: List[Dict[str, Any]] = []
        for node in class_node.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                attributes.extend(self._extract_assignment_records(node, scope="class"))
        return attributes

    def _extract_instance_attributes(self, class_node: ast.ClassDef) -> List[Dict[str, Any]]:
        attributes: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, str, int]] = set()
        for method in class_node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            owner_names = {"self", "cls"}
            if method.args.args:
                owner_names.add(method.args.args[0].arg)
            visitor = DirectInstanceAttributeVisitor(owner_names)
            for statement in method.body:
                visitor.visit(statement)
            for item in visitor.attributes:
                key = (item["owner"], item["name"], item["line"])
                if key in seen:
                    continue
                seen.add(key)
                attributes.append({**item, "defined_in": method.name})
        return attributes

    def _extract_assignment_records(
        self,
        node: Union[ast.Assign, ast.AnnAssign, ast.AugAssign],
        *,
        scope: str,
    ) -> List[Dict[str, Any]]:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = self.get_name(node.value)
            annotation = None
            type_comment = node.type_comment
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = self.get_name(node.value)
            annotation = self.get_name(node.annotation)
            type_comment = None
        else:
            targets = [node.target]
            value = self.get_name(node.value)
            annotation = None
            type_comment = None

        records: List[Dict[str, Any]] = []
        for target in targets:
            for variable_name in self._extract_target_names(target):
                records.append(
                    {
                        "name": variable_name,
                        "value": value,
                        "annotation": annotation,
                        "type_comment": type_comment,
                        "scope": scope,
                        "line": node.lineno,
                        "end_line": getattr(node, "end_lineno", node.lineno),
                        "is_constant": variable_name.isupper(),
                    }
                )
        return records

    def _extract_target_names(self, target: ast.AST) -> List[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, ast.Starred):
            return self._extract_target_names(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            names: List[str] = []
            for element in target.elts:
                names.extend(self._extract_target_names(element))
            return names
        if isinstance(target, ast.Attribute):
            value = self.get_name(target)
            return [value] if value else []
        return []

    def _extract_source_code(
        self,
        source_code: str,
        node: ast.AST,
        *,
        start_line: Optional[int] = None,
    ) -> str:
        start_val = start_line if start_line is not None else getattr(node, "lineno", 1)
        actual_start: int = int(start_val) if start_val is not None else 1
        end_val = getattr(node, "end_lineno", actual_start)
        end_line: int = int(end_val) if end_val is not None else actual_start
        lines = source_code.splitlines()
        return "\n".join(lines[actual_start - 1 : end_line])

    def _extract_signature(
        self,
        source_code: str,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    ) -> str:
        lines = source_code.splitlines()
        start = node.lineno - 1
        end = min(len(lines), getattr(node, "end_lineno", node.lineno))
        pieces: List[str] = []
        bracket_depth = 0
        for line in lines[start:end]:
            stripped = line.strip()
            pieces.append(stripped)
            bracket_depth += stripped.count("(") + stripped.count("[") + stripped.count("{")
            bracket_depth -= stripped.count(")") + stripped.count("]") + stripped.count("}")
            if bracket_depth <= 0 and stripped.endswith(":"):
                break
        return " ".join(pieces)

    def _extract_call(self, call: ast.Call) -> Dict[str, Any]:
        return {
            "name": self.get_name(call.func),
            "arguments": [self.get_name(argument) for argument in call.args],
            "keyword_arguments": {
                keyword.arg or "**": self.get_name(keyword.value)
                for keyword in call.keywords
            },
            "line": call.lineno,
            "end_line": getattr(call, "end_lineno", call.lineno),
            "column": call.col_offset,
        }

    def _extract_raise_type(self, raise_node: ast.Raise) -> Optional[str]:
        if raise_node.exc is None:
            return None
        if isinstance(raise_node.exc, ast.Call):
            return self.get_name(raise_node.exc.func)
        return self.get_name(raise_node.exc)

    def _extract_raise_message(self, raise_node: ast.Raise) -> Optional[str]:
        if isinstance(raise_node.exc, ast.Call) and raise_node.exc.args:
            return self.get_name(raise_node.exc.args[0])
        return None

    def _extract_loop(
        self,
        loop: Union[ast.For, ast.AsyncFor, ast.While],
    ) -> Dict[str, Any]:
        if isinstance(loop, ast.While):
            return {
                "type": "while",
                "condition": self.get_name(loop.test),
                "line": loop.lineno,
                "has_else": bool(loop.orelse),
            }
        return {
            "type": "async_for" if isinstance(loop, ast.AsyncFor) else "for",
            "target": self.get_name(loop.target),
            "iterable": self.get_name(loop.iter),
            "line": loop.lineno,
            "has_else": bool(loop.orelse),
        }

    def _extract_with_block(self, node: Union[ast.With, ast.AsyncWith]) -> Dict[str, Any]:
        return {
            "type": "async_with" if isinstance(node, ast.AsyncWith) else "with",
            "items": [
                {
                    "context": self.get_name(item.context_expr),
                    "target": self.get_name(item.optional_vars),
                }
                for item in node.items
            ],
            "line": node.lineno,
        }

    def _extract_match_block(self, node: ast.Match) -> Dict[str, Any]:
        return {
            "subject": self.get_name(node.subject),
            "case_count": len(node.cases),
            "patterns": [self.get_name(case.pattern) for case in node.cases],
            "line": node.lineno,
        }

    def _extract_handled_exceptions(self, try_blocks: List[ast.Try]) -> List[Dict[str, Any]]:
        handled: List[Dict[str, Any]] = []
        for try_block in try_blocks:
            for handler in try_block.handlers:
                handled.append(
                    {
                        "exception": self.get_name(handler.type),
                        "alias": handler.name,
                        "line": handler.lineno,
                    }
                )
        return handled

    def _get_method_kind(
        self,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    ) -> str:
        decorator_names = {self.get_name(item) for item in node.decorator_list}
        if "staticmethod" in decorator_names:
            return "static_method"
        if "classmethod" in decorator_names:
            return "class_method"
        if "property" in decorator_names:
            return "property"
        if any(name and name.endswith(".setter") for name in decorator_names):
            return "property_setter"
        if any(name and name.endswith(".deleter") for name in decorator_names):
            return "property_deleter"
        if "abstractmethod" in decorator_names or "abc.abstractmethod" in decorator_names:
            return "abstract_method"
        return "method"

    def _extract_route_metadata(
        self,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    ) -> Optional[Dict[str, Any]]:
        http_methods = {"get", "post", "put", "patch", "delete", "options", "head", "trace", "route", "websocket"}
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            decorator_name = self.get_name(decorator.func) or ""
            final_name = decorator_name.rsplit(".", 1)[-1].lower()
            if final_name not in http_methods:
                continue
            raw_path = self.get_name(decorator.args[0]) if decorator.args else None
            path = raw_path.strip("'\"") if raw_path else None
            keyword_arguments = {
                keyword.arg or "**": self.get_name(keyword.value)
                for keyword in decorator.keywords
            }
            return {
                "framework_hint": self._infer_route_framework(decorator_name),
                "decorator": decorator_name,
                "method": final_name.upper(),
                "path": path,
                "keyword_arguments": keyword_arguments,
                "line": decorator.lineno,
            }
        return None

    def _infer_route_framework(self, decorator_name: str) -> str:
        lowered = decorator_name.lower()
        if any(token in lowered for token in ("router.", "app.")):
            return "fastapi_or_flask"
        if "blueprint" in lowered:
            return "flask"
        return "unknown"

    def _extract_routes_from_symbols(
        self,
        classes: List[Dict[str, Any]],
        functions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        routes: List[Dict[str, Any]] = []
        for callable_data in self._iter_callables_from_records(classes, functions):
            if callable_data.get("route"):
                routes.append(
                    {
                        **callable_data["route"],
                        "qualified_name": callable_data["qualified_name"],
                    }
                )
        return routes

    def _extract_future_imports(self, tree: ast.Module) -> List[str]:
        values: List[str] = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                values.extend(alias.name for alias in node.names)
        return sorted(set(values))

    def _extract_all_exports(self, tree: ast.Module) -> List[str]:
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
                continue
            value = node.value
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                exports: List[str] = []
                for element in value.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        exports.append(element.value)
                return exports
        return []

    def _extract_type_aliases(self, tree: ast.Module) -> List[Dict[str, Any]]:
        aliases: List[Dict[str, Any]] = []
        type_alias_node = getattr(ast, "TypeAlias", None)
        for node in tree.body:
            if type_alias_node is not None and isinstance(node, type_alias_node):
                aliases.append(
                    {
                        "name": self.get_name(node.name),
                        "value": self.get_name(node.value),
                        "line": node.lineno,
                    }
                )
            elif (
                isinstance(node, ast.AnnAssign)
                and self.get_name(node.annotation) in {"TypeAlias", "typing.TypeAlias"}
            ):
                aliases.append(
                    {
                        "name": self.get_name(node.target),
                        "value": self.get_name(node.value),
                        "line": node.lineno,
                    }
                )
        return aliases

    def _extract_main_guard(self, tree: ast.Module) -> Optional[Dict[str, Any]]:
        for node in tree.body:
            if not isinstance(node, ast.If):
                continue
            expression = self.get_name(node.test)
            if expression in {"__name__ == '__main__'", '"__main__" == __name__', "'__main__' == __name__"}:
                return {
                    "line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "expression": expression,
                    "statement_count": len(node.body),
                }
        return None

    def _infer_class_protocols(self, node: ast.ClassDef) -> List[str]:
        bases = {self.get_name(base) or "" for base in node.bases}
        protocols: List[str] = []
        checks = {
            "dataclass": {"dataclass", "dataclasses.dataclass"},
            "pydantic_model": {"BaseModel", "pydantic.BaseModel"},
            "exception": {"Exception", "BaseException"},
            "enum": {"Enum", "IntEnum", "StrEnum", "enum.Enum", "enum.IntEnum"},
            "abc": {"ABC", "abc.ABC"},
            "protocol": {"Protocol", "typing.Protocol"},
        }
        decorators = {self.get_name(item) or "" for item in node.decorator_list}
        if decorators & checks["dataclass"]:
            protocols.append("dataclass")
        for protocol_name, candidates in checks.items():
            if protocol_name == "dataclass":
                continue
            if bases & candidates:
                protocols.append(protocol_name)
        return protocols

    # ------------------------------------------------------------------
    # Graph and project-level helpers
    # ------------------------------------------------------------------

    def _build_dependencies(self, files: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        parsed_files = [item for item in files if item.get("status") == "parsed"]
        known_modules = {
            item["module_name"]: item["path"]
            for item in parsed_files
            if item.get("module_name")
        }
        module_names = sorted(known_modules, key=len, reverse=True)

        internal: List[Dict[str, Any]] = []
        external: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, Any]] = []
        seen_internal: Set[Tuple[str, str, Tuple[str, ...], int]] = set()
        seen_external: Set[Tuple[str, str]] = set()

        for file_data in parsed_files:
            source_module = file_data.get("module_name", "")
            is_package = bool(file_data.get("is_package"))
            for import_detail in file_data.get("import_details", []):
                candidates = self._import_candidates(source_module, is_package, import_detail)
                matched_any = False

                for candidate_module, imported_symbols in candidates:
                    matched_module = self._match_known_module(candidate_module, module_names)
                    if matched_module is None:
                        continue
                    matched_any = True
                    target_path = known_modules[matched_module]
                    if target_path == file_data["path"]:
                        continue
                    remainder = candidate_module[len(matched_module) :].lstrip(".")
                    symbols = [item for item in ([remainder] if remainder else []) + imported_symbols if item]
                    key = (
                        file_data["path"],
                        target_path,
                        tuple(symbols),
                        int(import_detail.get("line", 0)),
                    )
                    if key in seen_internal:
                        continue
                    seen_internal.add(key)
                    internal.append(
                        {
                            "source": file_data["path"],
                            "target": target_path,
                            "source_module": source_module,
                            "target_module": matched_module,
                            "symbols": symbols,
                            "scope": import_detail.get("scope"),
                            "line": import_detail.get("line"),
                            "import_kind": import_detail.get("kind"),
                        }
                    )

                if matched_any:
                    continue

                root_names = sorted({candidate.split(".", 1)[0] for candidate, _ in candidates if candidate})
                if not root_names:
                    unresolved.append(
                        {
                            "source": file_data["path"],
                            "import": import_detail,
                            "reason": "empty_or_dynamic_import",
                        }
                    )
                    continue

                for root_name in root_names:
                    external_key = (file_data["path"], root_name)
                    if external_key in seen_external:
                        continue
                    seen_external.add(external_key)
                    external.append(
                        {
                            "source": file_data["path"],
                            "package": root_name,
                            "scope": import_detail.get("scope"),
                            "line": import_detail.get("line"),
                        }
                    )

        return {
            "internal": internal,
            "external": external,
            "unresolved": unresolved,
        }

    def _import_candidates(
        self,
        source_module: str,
        is_package: bool,
        import_detail: Dict[str, Any],
    ) -> List[Tuple[str, List[str]]]:
        kind = import_detail.get("kind")
        names = import_detail.get("names", [])
        if kind == "import":
            return [
                (str(alias.get("name") or ""), [])
                for alias in names
                if alias.get("name")
            ]

        level = int(import_detail.get("level") or 0)
        module = str(import_detail.get("module") or "")
        if level > 0:
            package_parts = source_module.split(".") if is_package else source_module.split(".")[:-1]
            remove_count = max(0, level - 1)
            if remove_count > len(package_parts):
                base_parts: List[str] = []
            else:
                base_parts = package_parts[: len(package_parts) - remove_count]
            if module:
                base_parts.extend(module.split("."))
            base_module = ".".join(item for item in base_parts if item)
        else:
            base_module = module

        imported_names = [str(alias.get("name") or "") for alias in names if alias.get("name")]
        candidates: List[Tuple[str, List[str]]] = []
        if base_module:
            candidates.append((base_module, imported_names))
        for imported_name in imported_names:
            if imported_name == "*":
                continue
            candidate = ".".join(item for item in (base_module, imported_name) if item)
            candidates.append((candidate, []))
        return candidates

    def _match_known_module(self, candidate: str, module_names: Sequence[str]) -> Optional[str]:
        for module_name in module_names:
            if candidate == module_name or candidate.startswith(f"{module_name}."):
                return module_name
        return None

    def _build_project_symbol_index(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        symbols: List[Dict[str, Any]] = []
        for file_data in files:
            if file_data.get("status") != "parsed":
                continue
            for symbol in file_data.get("symbols", []):
                symbols.append({**symbol, "file_path": file_data["path"], "module_name": file_data.get("module_name")})
        return symbols

    def _build_file_import_map(self, file_data: Dict[str, Any]) -> Dict[str, str]:
        source_module = file_data.get("module_name", "")
        is_package = bool(file_data.get("is_package"))
        import_map: Dict[str, str] = {}

        for import_detail in file_data.get("import_details", []):
            kind = import_detail.get("kind")
            names = import_detail.get("names", [])
            if kind == "import":
                for alias in names:
                    name = alias.get("name")
                    if not name:
                        continue
                    asname = alias.get("asname")
                    if asname:
                        import_map[asname] = name
                    else:
                        import_map[name] = name
                        parts = name.split(".")
                        import_map[parts[0]] = parts[0]
            elif kind == "from":
                level = int(import_detail.get("level") or 0)
                module = str(import_detail.get("module") or "")
                if level > 0:
                    package_parts = source_module.split(".") if is_package else source_module.split(".")[:-1]
                    remove_count = max(0, level - 1)
                    if remove_count > len(package_parts):
                        base_parts: List[str] = []
                    else:
                        base_parts = package_parts[: len(package_parts) - remove_count]
                    if module:
                        base_parts.extend(module.split("."))
                    base_module = ".".join(item for item in base_parts if item)
                else:
                    base_module = module

                for alias in names:
                    imported_name = alias.get("name")
                    if not imported_name or imported_name == "*":
                        continue
                    asname = alias.get("asname")
                    local_name = asname if asname else imported_name
                    target = f"{base_module}.{imported_name}" if base_module else imported_name
                    import_map[local_name] = target

        return import_map

    def _build_call_graph(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        callable_records = list(self._iter_project_callables(files))
        class_records = list(self._iter_project_classes(files))

        qualified_names = {
            item["qualified_name"]: item for item in callable_records if item.get("qualified_name")
        }
        class_qualified_names = {
            item["qualified_name"]: item for item in class_records if item.get("qualified_name")
        }

        simple_name_index: Dict[str, List[str]] = defaultdict(list)
        for qname in qualified_names:
            simple_name_index[qname.rsplit(".", 1)[-1]].append(qname)
        for qname in class_qualified_names:
            if qname not in simple_name_index[qname.rsplit(".", 1)[-1]]:
                simple_name_index[qname.rsplit(".", 1)[-1]].append(qname)

        file_import_maps: Dict[str, Dict[str, str]] = {
            file_data["path"]: self._build_file_import_map(file_data)
            for file_data in files
            if file_data.get("status") == "parsed" and "path" in file_data
        }

        edges: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, str, int]] = set()
        for callable_data in callable_records:
            source = callable_data["qualified_name"]
            parent = callable_data.get("parent_qualified_name") or ""
            module_name = callable_data.get("module_name") or ""
            file_path = callable_data.get("file_path") or ""
            import_map = file_import_maps.get(file_path, {})

            for call in callable_data.get("calls", []):
                call_name = call.get("name")
                if not call_name:
                    continue
                target, resolution = self._resolve_call_target(
                    call_name=call_name,
                    parent_qualified_name=parent,
                    module_name=module_name,
                    qualified_names=qualified_names,
                    simple_name_index=simple_name_index,
                    import_map=import_map,
                    class_qualified_names=class_qualified_names,
                )
                if target is None:
                    continue
                key = (source, target, int(call.get("line") or 0))
                if key in seen:
                    continue
                seen.add(key)
                edges.append(
                    {
                        "source": source,
                        "target": target,
                        "call_name": call_name,
                        "line": call.get("line"),
                        "resolution": resolution,
                    }
                )
        return edges

    def _resolve_call_target(
        self,
        *,
        call_name: str,
        parent_qualified_name: str = "",
        module_name: str = "",
        qualified_names: Dict[str, Dict[str, Any]],
        simple_name_index: Dict[str, List[str]],
        import_map: Optional[Dict[str, str]] = None,
        class_qualified_names: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        imports = import_map or {}
        classes = class_qualified_names or {}

        # 1. Check import map
        if call_name in imports:
            resolved = imports[call_name]
            if resolved in qualified_names:
                return resolved, "imported_callable"
            if resolved in classes:
                return resolved, "constructor"

        if "." in call_name:
            parts = call_name.split(".")
            for i in range(len(parts) - 1, 0, -1):
                prefix = ".".join(parts[:i])
                if prefix in imports:
                    remainder = ".".join(parts[i:])
                    candidate = f"{imports[prefix]}.{remainder}"
                    if candidate in qualified_names:
                        return candidate, "imported_callable"
                    if candidate in classes:
                        return candidate, "constructor"

        # 2. Contextual intra-class / intra-module candidates
        candidates: List[Tuple[str, str]] = []
        if call_name.startswith("self.") and parent_qualified_name:
            candidates.append((f"{parent_qualified_name}.{call_name.split('.', 1)[1]}", "exact_or_contextual"))
        elif call_name.startswith("cls.") and parent_qualified_name:
            candidates.append((f"{parent_qualified_name}.{call_name.split('.', 1)[1]}", "exact_or_contextual"))
        else:
            if parent_qualified_name:
                candidates.append((f"{parent_qualified_name}.{call_name}", "exact_or_contextual"))
            if module_name:
                candidates.append((f"{module_name}.{call_name}", "exact_or_contextual"))

        for candidate, res_kind in candidates:
            if candidate in qualified_names:
                return candidate, res_kind
            if candidate in classes:
                return candidate, "constructor"

        # 3. Exact match
        if call_name in qualified_names:
            return call_name, "exact_or_contextual"
        if call_name in classes:
            return call_name, "constructor"

        # 4. Fallback to unique simple name
        simple_name = call_name.rsplit(".", 1)[-1]
        matches = simple_name_index.get(simple_name, [])
        if len(matches) == 1:
            target = matches[0]
            res_kind = "constructor" if target in classes else "unique_simple_name"
            return target, res_kind

        return None, None

    def _build_inheritance_graph(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        class_records = list(self._iter_project_classes(files))
        qualified_names = {item["qualified_name"] for item in class_records if item.get("qualified_name")}
        simple_index: Dict[str, List[str]] = defaultdict(list)
        for qualified_name in qualified_names:
            simple_index[qualified_name.rsplit(".", 1)[-1]].append(qualified_name)

        file_import_maps: Dict[str, Dict[str, str]] = {
            file_data["path"]: self._build_file_import_map(file_data)
            for file_data in files
            if file_data.get("status") == "parsed" and "path" in file_data
        }

        edges: List[Dict[str, Any]] = []
        for class_data in class_records:
            file_path = class_data.get("file_path", "")
            module_name = class_data.get("module_name", "")
            import_map = file_import_maps.get(file_path, {})
            for base in class_data.get("bases", []):
                if not base:
                    continue
                target = base if base in qualified_names else None
                if target is None and base in import_map:
                    candidate = import_map[base]
                    if candidate in qualified_names:
                        target = candidate
                if target is None and module_name:
                    candidate = f"{module_name}.{base}"
                    if candidate in qualified_names:
                        target = candidate
                if target is None:
                    matches = simple_index.get(base.rsplit(".", 1)[-1], [])
                    if len(matches) == 1:
                        target = matches[0]
                edges.append(
                    {
                        "source": class_data["qualified_name"],
                        "target": target,
                        "base": base,
                        "resolved": target is not None,
                    }
                )
        return edges

    def _collect_project_routes(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        routes: List[Dict[str, Any]] = []
        for file_data in files:
            if file_data.get("status") != "parsed":
                continue
            for route in file_data.get("routes", []):
                routes.append({**route, "file_path": file_data["path"]})
        return routes

    def _build_project_statistics(self, project_result: Dict[str, Any]) -> Dict[str, Any]:
        parsed_files = [item for item in project_result["files"] if item.get("status") == "parsed"]
        symbol_counts = Counter(item.get("kind", "unknown") for item in project_result.get("symbols", []))
        return {
            "parsed_python_files": len(parsed_files),
            "unparsed_text_files": sum(1 for item in project_result["files"] if item.get("status") == "unparsed"),
            "error_files": project_result.get("error_count", 0),
            "total_source_lines": sum(int(item.get("source_line_count") or 0) for item in project_result["files"]),
            "total_logical_python_lines": sum(int(item.get("logical_line_count") or 0) for item in parsed_files),
            "symbol_counts": dict(sorted(symbol_counts.items())),
            "internal_dependency_count": len(project_result.get("dependencies", [])),
            "external_dependency_count": len(project_result.get("external_dependencies", [])),
            "call_graph_edge_count": len(project_result.get("call_graph", [])),
            "route_count": len(project_result.get("routes", [])),
        }

    # ------------------------------------------------------------------
    # File discovery, reuse, and serialization helpers
    # ------------------------------------------------------------------

    def _discover_project_files(self, source_directory: Path) -> Tuple[List[Path], List[Dict[str, Any]]]:
        candidates: List[Path] = []
        skipped: List[Dict[str, Any]] = []
        ignored_directories = set(self.config.ignored_directories)

        for path in source_directory.rglob("*"):
            if not path.is_file():
                continue
            relative_path = path.relative_to(source_directory)
            if any(part in ignored_directories for part in relative_path.parts):
                continue
            if path.is_symlink() and not self.config.follow_symlinks:
                skipped.append({"path": relative_path.as_posix(), "reason": "symlink"})
                continue
            if self._is_sensitive_file(path):
                skipped.append({"path": relative_path.as_posix(), "reason": "sensitive_file"})
                continue
            try:
                size = path.stat().st_size
            except OSError as error:
                skipped.append({"path": relative_path.as_posix(), "reason": f"stat_error:{error}"})
                continue
            if size > self.config.max_file_bytes:
                skipped.append({"path": relative_path.as_posix(), "reason": "file_too_large", "size": size})
                continue

            language = self._detect_language(path)
            if language == "python" or (self.config.include_non_python_files and language != "binary"):
                candidates.append(path)

        candidates.sort(key=lambda item: item.relative_to(source_directory).as_posix())
        return candidates, skipped

    def _detect_language(self, file_path: Path) -> str:
        lower_name = file_path.name.lower()
        if file_path.suffix.lower() in PYTHON_SUFFIXES:
            return "python"
        if lower_name in SPECIAL_TEXT_FILENAMES:
            return SPECIAL_TEXT_FILENAMES[lower_name]
        return TEXT_LANGUAGE_BY_SUFFIX.get(file_path.suffix.lower(), "binary")

    def _is_sensitive_file(self, file_path: Path) -> bool:
        return file_path.name.lower() in SENSITIVE_FILENAMES or file_path.suffix.lower() in SENSITIVE_SUFFIXES

    def _read_python_source(self, file_path: Path) -> Tuple[str, str]:
        with tokenize.open(str(file_path)) as file:
            return file.read(), file.encoding

    def _read_text_source(self, file_path: Path) -> Tuple[str, str]:
        data = file_path.read_bytes()
        if b"\x00" in data[:8_192]:
            raise ValueError("Binary file detected")
        try:
            return data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace"), "utf-8-replace"

    def _build_unparsed_text_entry(
        self,
        *,
        project_id: str,
        file_path: Path,
        root: Path,
        source_code: str,
        encoding: str,
        language: str,
    ) -> Dict[str, Any]:
        relative_path = file_path.relative_to(root).as_posix()
        return {
            "id": self._generate_hash(f"{project_id}:{relative_path}"),
            "path": relative_path,
            "module_name": None,
            "status": "unparsed",
            "language": language,
            "encoding": encoding,
            "file_size_bytes": file_path.stat().st_size,
            "content_hash": self._generate_hash(source_code),
            "source_line_count": len(source_code.splitlines()),
            "imports": [],
            "classes": [],
            "functions": [],
            "comments": [],
            "reused": False,
        }

    def _build_error_entry(
        self,
        *,
        project_id: str,
        relative_path: Path,
        error: Exception,
    ) -> Dict[str, Any]:
        error_data: Dict[str, Any] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        if isinstance(error, SyntaxError):
            error_data.update(
                {
                    "line": error.lineno,
                    "column": error.offset,
                    "end_line": getattr(error, "end_lineno", None),
                    "end_column": getattr(error, "end_offset", None),
                    "source_line": error.text.strip() if error.text else None,
                }
            )
        relative_path_string = relative_path.as_posix()
        return {
            "id": self._generate_hash(f"{project_id}:{relative_path_string}"),
            "path": relative_path_string,
            "module_name": (
                self._get_module_name(relative_path)
                if relative_path.suffix.lower() in PYTHON_SUFFIXES
                else None
            ),
            "status": "error",
            "language": self._detect_language(relative_path),
            "error": error_data,
        }

    def _load_previous_ast(
        self,
        project_id: Optional[str] = None,
        *,
        ast_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        if ast_path is not None:
            target_path = Path(ast_path)
        elif project_id is not None:
            try:
                target_path = get_ast_path(project_id)
            except Exception:
                return {}
        else:
            return {}

        if not target_path.exists():
            return {}
        try:
            value = json.loads(target_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return value if isinstance(value, dict) else {}

    def _can_reuse_previous_project(self, previous_ast: Dict[str, Any]) -> bool:
        return (
            previous_ast.get("schema_version") == self.SCHEMA_VERSION
            and previous_ast.get("parser_config_signature") == self._config_signature()
        )

    def _config_signature(self) -> str:
        value = json.dumps(asdict(self.config), sort_keys=True)
        return self._generate_hash(value)[:20]

    # ------------------------------------------------------------------
    # Flattening and iteration helpers
    # ------------------------------------------------------------------

    def _flatten_import_details(self, details: List[Dict[str, Any]]) -> List[str]:
        values: Set[str] = set()
        for detail in details:
            if detail.get("kind") == "import":
                for alias in detail.get("names", []):
                    name = alias.get("name")
                    if name:
                        values.add(str(name))
                continue
            prefix = "." * int(detail.get("level") or 0)
            module = str(detail.get("module") or "")
            base = f"{prefix}{module}"
            for alias in detail.get("names", []):
                name = alias.get("name")
                if not name:
                    continue
                values.add(f"{base}.{name}" if base else str(name))
        return sorted(values)

    def _flatten_file_symbols(self, file_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        symbols: List[Dict[str, Any]] = []

        def add_callable(item: Dict[str, Any]) -> None:
            symbols.append(self._symbol_summary(item, kind=item.get("callable_kind", "callable")))
            for nested in item.get("nested_functions", []):
                add_callable(nested)
            for nested_class in item.get("nested_classes", []):
                add_class(nested_class)

        def add_class(item: Dict[str, Any]) -> None:
            symbols.append(self._symbol_summary(item, kind="class"))
            for method in item.get("methods", []):
                add_callable(method)
            for nested_class in item.get("nested_classes", []):
                add_class(nested_class)

        for class_data in file_data.get("classes", []):
            add_class(class_data)
        for function_data in file_data.get("functions", []):
            add_callable(function_data)
        return symbols

    def _symbol_summary(self, item: Dict[str, Any], *, kind: str) -> Dict[str, Any]:
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "qualified_name": item.get("qualified_name"),
            "parent_qualified_name": item.get("parent_qualified_name"),
            "kind": kind,
            "node_type": item.get("node_type"),
            "start_line": item.get("start_line"),
            "end_line": item.get("end_line"),
            "content_hash": item.get("content_hash"),
            "is_async": item.get("is_async", False),
            "docstring": item.get("docstring"),
        }

    def _iter_callables_from_records(
        self,
        classes: List[Dict[str, Any]],
        functions: List[Dict[str, Any]],
    ) -> Iterable[Dict[str, Any]]:
        for function_data in functions:
            yield function_data
            yield from self._iter_nested_callables(function_data)
        for class_data in classes:
            for method in class_data.get("methods", []):
                yield method
                yield from self._iter_nested_callables(method)
            for nested_class in class_data.get("nested_classes", []):
                yield from self._iter_callables_from_records([nested_class], [])

    def _iter_nested_callables(self, callable_data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        for nested in callable_data.get("nested_functions", []):
            yield nested
            yield from self._iter_nested_callables(nested)
        for nested_class in callable_data.get("nested_classes", []):
            yield from self._iter_callables_from_records([nested_class], [])

    def _iter_project_callables(self, files: List[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        for file_data in files:
            if file_data.get("status") != "parsed":
                continue
            module_name = file_data.get("module_name")
            for callable_data in self._iter_callables_from_records(
                file_data.get("classes", []),
                file_data.get("functions", []),
            ):
                yield {**callable_data, "module_name": module_name, "file_path": file_data["path"]}

    def _iter_project_classes(self, files: List[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        def walk(class_data: Dict[str, Any], module_name: str, file_path: str) -> Iterable[Dict[str, Any]]:
            yield {**class_data, "module_name": module_name, "file_path": file_path}
            for nested in class_data.get("nested_classes", []):
                yield from walk(nested, module_name, file_path)

        for file_data in files:
            if file_data.get("status") != "parsed":
                continue
            module_name = file_data.get("module_name", "")
            file_path = file_data.get("path", "")
            for class_data in file_data.get("classes", []):
                yield from walk(class_data, module_name, file_path)

    # ------------------------------------------------------------------
    # General utilities
    # ------------------------------------------------------------------

    def _decorated_start_line(self, node: Union[ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef]) -> int:
        decorator_lines = [getattr(item, "lineno", node.lineno) for item in node.decorator_list]
        return min([node.lineno, *decorator_lines])

    def _get_module_name(self, relative_path: Path) -> str:
        parts = list(relative_path.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    def _get_package_name(self, module_name: str, is_package: bool) -> str:
        if not module_name:
            return ""
        if is_package:
            return module_name
        return module_name.rsplit(".", 1)[0] if "." in module_name else ""

    def _truncate_docstring(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if len(value) <= self.config.max_docstring_characters:
            return value
        return value[: self.config.max_docstring_characters] + "..."

    def _count_logical_python_lines(self, source_code: str) -> int:
        count = 0
        try:
            tokens = tokenize.generate_tokens(io.StringIO(source_code).readline)
            for token in tokens:
                if token.type == tokenize.NEWLINE:
                    count += 1
        except (tokenize.TokenError, IndentationError):
            return 0
        return count

    def _count_parameters(self, parameters: Dict[str, Any]) -> int:
        count = len(parameters.get("positional", [])) + len(parameters.get("keyword_only", []))
        if parameters.get("vararg"):
            count += 1
        if parameters.get("kwarg"):
            count += 1
        return count

    def _build_file_metrics(
        self,
        tree: ast.Module,
        source_code: str,
        classes: List[Dict[str, Any]],
        functions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        callables = list(self._iter_callables_from_records(classes, functions))
        return {
            "source_line_count": len(source_code.splitlines()),
            "logical_line_count": self._count_logical_python_lines(source_code),
            "class_count": sum(1 for _ in self._walk_class_records(classes)),
            "callable_count": len(callables),
            "top_level_function_count": len(functions),
            "import_statement_count": sum(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)),
            "average_callable_complexity": (
                round(
                    sum(int(item.get("metrics", {}).get("cyclomatic_complexity", 1)) for item in callables)
                    / len(callables),
                    2,
                )
                if callables
                else 0.0
            ),
            "maximum_callable_complexity": max(
                [int(item.get("metrics", {}).get("cyclomatic_complexity", 1)) for item in callables],
                default=0,
            ),
        }

    def _walk_class_records(self, classes: List[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        for class_data in classes:
            yield class_data
            yield from self._walk_class_records(class_data.get("nested_classes", []))

    def _generate_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


ast_parser = ASTParser()
