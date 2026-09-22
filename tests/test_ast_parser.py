from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from change_graph.ast_parser import ASTParser, ParserConfig, ast_parser


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a sample multi-file Python project in a temporary directory."""
    pkg_dir = tmp_path / "sample_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text('"""Sample package init."""\n', encoding="utf-8")

    # Base classes module
    base_file = pkg_dir / "base.py"
    base_file.write_text(
        '''from abc import ABC, abstractmethod

class BaseHandler(ABC):
    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def handle(self) -> str:
        pass
''',
        encoding="utf-8",
    )

    # Models module with a class constructor
    models_file = pkg_dir / "models.py"
    models_file.write_text(
        '''class TaskRecord:
    def __init__(self, task_id: str, priority: int = 1) -> None:
        self.task_id = task_id
        self.priority = priority

    def summary(self) -> str:
        return f"{self.task_id}:{self.priority}"
''',
        encoding="utf-8",
    )

    # Services module importing models and base
    services_file = pkg_dir / "services.py"
    services_file.write_text(
        '''from .base import BaseHandler
from .models import TaskRecord

def standalone_helper(msg: str) -> str:
    return msg.upper()

class TaskHandler(BaseHandler):
    def __init__(self) -> None:
        super().__init__("TaskHandler")

    def helper(self) -> str:
        return standalone_helper("from_helper")

    def handle(self) -> str:
        record = TaskRecord("task-001", priority=2)
        h = self.helper()
        return f"{record.summary()}_{h}"
''',
        encoding="utf-8",
    )

    # API module with aliased import and FastAPI-style routes
    api_file = pkg_dir / "api.py"
    api_file.write_text(
        '''from .services import TaskHandler as TH
import sample_pkg.services as srv

class APIRouter:
    def get(self, path: str):
        def decorator(f):
            return f
        return decorator

router = APIRouter()

@router.get("/tasks")
def list_tasks() -> str:
    handler = TH()
    return handler.handle()

@router.get("/helper")
def call_helper() -> str:
    return srv.standalone_helper("api_call")
''',
        encoding="utf-8",
    )

    return tmp_path


def test_parse_directory_basic(sample_project: Path) -> None:
    """Verify parse_directory parses arbitrary local folders without temp_repos paths."""
    parser = ASTParser()
    result = parser.parse_directory(sample_project)

    assert result["parser"] == "python_ast_parser"
    assert result["total_python_file_count"] >= 5
    assert result["file_count"] >= 5
    assert result["error_count"] == 0
    assert result["project_id"] == sample_project.name


def test_parse_directory_save_output(sample_project: Path, tmp_path: Path) -> None:
    """Verify parse_directory saves AST output to a designated path when requested."""
    parser = ASTParser()
    output_file = tmp_path / "custom_ast.json"

    result = parser.parse_directory(
        sample_project,
        project_id="test_project",
        save_output=True,
        output_path=output_file,
    )

    assert output_file.exists()
    saved_data = json.loads(output_file.read_text(encoding="utf-8"))
    assert saved_data["project_id"] == "test_project"
    assert len(saved_data["files"]) == len(result["files"])


def test_parse_directory_invalid_paths(tmp_path: Path) -> None:
    """Verify parse_directory raises appropriate errors on nonexistent or non-dir paths."""
    parser = ASTParser()
    nonexistent = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        parser.parse_directory(nonexistent)

    file_path = tmp_path / "a_file.txt"
    file_path.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        parser.parse_directory(file_path)


def test_call_graph_cross_module_and_constructors(sample_project: Path) -> None:
    """Verify call graph resolves imported functions, constructors, methods, and aliases."""
    result = ast_parser.parse_directory(sample_project)
    call_graph = result["call_graph"]
    assert len(call_graph) > 0

    # Index edges by (source, target)
    edges: Dict[tuple[str, str], Dict[str, Any]] = {
        (edge["source"], edge["target"]): edge for edge in call_graph
    }

    # 1. TaskHandler.handle -> TaskRecord (constructor resolution)
    assert ("sample_pkg.services.TaskHandler.handle", "sample_pkg.models.TaskRecord") in edges
    record_edge = edges[("sample_pkg.services.TaskHandler.handle", "sample_pkg.models.TaskRecord")]
    assert record_edge["resolution"] == "constructor"
    assert record_edge["call_name"] == "TaskRecord"

    # 2. TaskHandler.handle -> TaskHandler.helper (intra-class method resolution)
    assert ("sample_pkg.services.TaskHandler.handle", "sample_pkg.services.TaskHandler.helper") in edges
    helper_edge = edges[("sample_pkg.services.TaskHandler.handle", "sample_pkg.services.TaskHandler.helper")]
    assert helper_edge["resolution"] == "exact_or_contextual"

    # 3. TaskHandler.helper -> standalone_helper (intra-module / imported function resolution)
    assert ("sample_pkg.services.TaskHandler.helper", "sample_pkg.services.standalone_helper") in edges
    fn_edge = edges[("sample_pkg.services.TaskHandler.helper", "sample_pkg.services.standalone_helper")]
    assert fn_edge["resolution"] in {"exact_or_contextual", "imported_callable", "unique_simple_name"}

    # 4. list_tasks -> TaskHandler (aliased constructor import: from .services import TaskHandler as TH; TH())
    assert ("sample_pkg.api.list_tasks", "sample_pkg.services.TaskHandler") in edges
    aliased_edge = edges[("sample_pkg.api.list_tasks", "sample_pkg.services.TaskHandler")]
    assert aliased_edge["resolution"] == "constructor"
    assert aliased_edge["call_name"] == "TH"

    # 5. call_helper -> standalone_helper (aliased module: import sample_pkg.services as srv; srv.standalone_helper())
    assert ("sample_pkg.api.call_helper", "sample_pkg.services.standalone_helper") in edges
    mod_alias_edge = edges[("sample_pkg.api.call_helper", "sample_pkg.services.standalone_helper")]
    assert mod_alias_edge["resolution"] == "imported_callable"
    assert mod_alias_edge["call_name"] == "srv.standalone_helper"


def test_inheritance_graph(sample_project: Path) -> None:
    """Verify class inheritance graph resolution across modules."""
    result = ast_parser.parse_directory(sample_project)
    inheritance_graph = result["inheritance_graph"]

    inheritance_map = {edge["source"]: edge for edge in inheritance_graph}

    # TaskHandler inherits from BaseHandler
    assert "sample_pkg.services.TaskHandler" in inheritance_map
    edge = inheritance_map["sample_pkg.services.TaskHandler"]
    assert edge["target"] == "sample_pkg.base.BaseHandler"
    assert edge["base"] == "BaseHandler"
    assert edge["resolved"] is True

    # BaseHandler inherits from ABC
    assert "sample_pkg.base.BaseHandler" in inheritance_map
    abc_edge = inheritance_map["sample_pkg.base.BaseHandler"]
    assert abc_edge["base"] == "ABC"


def test_fastapi_routes(sample_project: Path) -> None:
    """Verify FastAPI / router endpoint extraction."""
    result = ast_parser.parse_directory(sample_project)
    routes = result["routes"]
    assert len(routes) == 2

    route_paths = {r["path"]: r for r in routes}
    assert "/tasks" in route_paths
    assert route_paths["/tasks"]["method"] == "GET"
    assert route_paths["/tasks"]["qualified_name"] == "sample_pkg.api.list_tasks"

    assert "/helper" in route_paths
    assert route_paths["/helper"]["method"] == "GET"
    assert route_paths["/helper"]["qualified_name"] == "sample_pkg.api.call_helper"


def test_syntax_error_resilience(tmp_path: Path) -> None:
    """Verify files with syntax errors are recorded cleanly without aborting project parse."""
    bad_dir = tmp_path / "bad_project"
    bad_dir.mkdir()
    (bad_dir / "valid.py").write_text("def ok(): return 1\n", encoding="utf-8")
    (bad_dir / "broken.py").write_text("def broken(: syntax error\n", encoding="utf-8")

    parser = ASTParser(config=ParserConfig(reuse_unchanged_files=False))
    result = parser.parse_directory(bad_dir)

    assert result["file_count"] == 1
    assert result["error_count"] == 1
    statuses = {f["path"]: f["status"] for f in result["files"]}
    assert statuses["valid.py"] == "parsed"
    assert statuses["broken.py"] == "error"
