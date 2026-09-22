from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from mcp_server.code_intel_server import create_code_intel_server
from mcp_server.docs_workspace_server import create_docs_workspace_server

SAMPLE_DIFF = """diff --git a/services.py b/services.py
index 1234567..89abcde 100644
--- a/services.py
+++ b/services.py
@@ -10,3 +10,4 @@
 def process_payment(amount: float, currency: str = "USD") -> bool:
     \"\"\"Process customer payment.\"\"\"
+    logger.info(f"Processing {amount} {currency}")
     return True
"""


async def call_mcp_tool(server: Any, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to invoke a FastMCP tool and unwrap the result payload."""
    res = await server.call_tool(name, args)
    payload = res[1]
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


@pytest.fixture
def mock_source_repo(tmp_path: Path) -> Path:
    """Creates a sample source code repository structure."""
    src_dir = tmp_path / "src_repo"
    src_dir.mkdir()

    services_file = src_dir / "services.py"
    services_file.write_text(
        '''from utils import send_receipt

class PaymentHandler:
    def __init__(self, gateway: str) -> None:
        self.gateway = gateway

    def handle(self, order_id: str) -> bool:
        return process_payment(100.0)

def process_payment(amount: float, currency: str = "USD") -> bool:
    """Process customer payment."""
    send_receipt("user@example.com")
    return True
''',
        encoding="utf-8",
    )

    utils_file = src_dir / "utils.py"
    utils_file.write_text(
        '''def send_receipt(email: str) -> None:
    """Send receipt email to user."""
    pass
''',
        encoding="utf-8",
    )

    return src_dir


@pytest.fixture
def mock_docs_repo(tmp_path: Path) -> Path:
    """Creates a sample documentation repository structure."""
    docs_dir = tmp_path / "docs_repo"
    docs_dir.mkdir()

    intro_file = docs_dir / "README.md"
    intro_file.write_text(
        """# API Documentation
Welcome to the developer documentation.

## Payments
Call `process_payment(amount, currency="USD")` to process transactions.
""",
        encoding="utf-8",
    )

    api_dir = docs_dir / "api"
    api_dir.mkdir()
    payments_doc = api_dir / "payments.md"
    payments_doc.write_text(
        """# Payment Processing
The payment gateway handles credit cards.

```python
success = process_payment(50.0, "EUR")
```
""",
        encoding="utf-8",
    )

    return docs_dir


# ==================== Code Intelligence Server Tests ====================


@pytest.mark.asyncio
async def test_code_intel_diff_summary(mock_source_repo: Path) -> None:
    server = create_code_intel_server(mock_source_repo, raw_diff=SAMPLE_DIFF)
    data = await call_mcp_tool(server, "get_diff_summary", {})

    assert data["total_files_changed"] == 1
    file_info = data["files"][0]
    assert file_info["path"] == "services.py"
    assert len(file_info["hunks"]) == 1


@pytest.mark.asyncio
async def test_code_intel_impacted_symbols(mock_source_repo: Path) -> None:
    server = create_code_intel_server(mock_source_repo, raw_diff=SAMPLE_DIFF)
    data = await call_mcp_tool(server, "get_impacted_symbols", {})

    impacted = data["impacted_symbols"]
    assert len(impacted) >= 1
    sym = next(s for s in impacted if s["symbol_name"] == "process_payment")
    assert sym["kind"] == "function"
    assert "process_payment" in sym["signature"]
    # Verify downstream caller was detected from PaymentHandler.handle
    assert any("PaymentHandler" in caller or "handle" in caller for caller in sym["downstream_callers"])


@pytest.mark.asyncio
async def test_code_intel_impacted_symbols_docstring_only(mock_source_repo: Path) -> None:
    docstring_diff = """diff --git a/services.py b/services.py
index 1234567..89abcde 100644
--- a/services.py
+++ b/services.py
@@ -10,2 +10,2 @@
 def process_payment(amount: float, currency: str = "USD") -> bool:
-    \"\"\"Process customer payment.\"\"\"
+    \"\"\"Process customer payment with currency support.\"\"\"
"""
    server = create_code_intel_server(mock_source_repo, raw_diff=docstring_diff)
    data = await call_mcp_tool(server, "get_impacted_symbols", {})
    impacted = data["impacted_symbols"]
    assert len(impacted) == 1
    sym = impacted[0]
    assert sym["symbol_name"] == "process_payment"
    assert sym["is_docstring_only"] is True


@pytest.mark.asyncio
async def test_code_intel_symbol_info(mock_source_repo: Path) -> None:
    server = create_code_intel_server(mock_source_repo)

    # Test function info
    fn_data = await call_mcp_tool(
        server, "get_symbol_info", {"file_path": "services.py", "symbol_name": "process_payment"}
    )
    assert fn_data["symbol_name"] == "process_payment"
    assert fn_data["kind"] == "function"
    assert "Process customer payment." in fn_data["docstring"]

    # Test method info (Class.method)
    method_data = await call_mcp_tool(
        server, "get_symbol_info", {"file_path": "services.py", "symbol_name": "PaymentHandler.handle"}
    )
    assert method_data["symbol_name"] == "PaymentHandler.handle"
    assert method_data["kind"] == "method"
    assert method_data["parent_class"] == "PaymentHandler"

    # Test non-existent symbol
    res_err = await call_mcp_tool(
        server, "get_symbol_info", {"file_path": "services.py", "symbol_name": "non_existent_func"}
    )
    assert "error" in res_err


@pytest.mark.asyncio
async def test_code_intel_call_graph(mock_source_repo: Path) -> None:
    server = create_code_intel_server(mock_source_repo)
    data = await call_mcp_tool(server, "get_call_graph", {"symbol_name": "process_payment"})

    assert data["symbol"] == "process_payment"
    # Callers should include PaymentHandler.handle
    assert data["callers_count"] >= 1
    # Callees should include send_receipt
    assert data["callees_count"] >= 1
    assert any("send_receipt" in c["callee"] or c["call_name"] == "send_receipt" for c in data["callees"])


# ==================== Docs Workspace Server Tests ====================


@pytest.mark.asyncio
async def test_docs_workspace_search(mock_docs_repo: Path) -> None:
    server = create_docs_workspace_server(mock_docs_repo)

    # Search for function name
    data = await call_mcp_tool(server, "search_docs", {"query": "process_payment"})
    assert data["total_matches"] >= 2
    matched_files = {m["file_path"] for m in data["matches"]}
    assert "README.md" in matched_files
    assert "api/payments.md" in matched_files

    # Search with special characters (safe literal matching)
    res_special = await call_mcp_tool(server, "search_docs", {"query": 'currency="USD"'})
    assert res_special["total_matches"] == 1


@pytest.mark.asyncio
async def test_docs_workspace_list_files(mock_docs_repo: Path) -> None:
    server = create_docs_workspace_server(mock_docs_repo)
    data = await call_mcp_tool(server, "list_doc_files", {})

    assert data["total_files"] >= 2
    paths = {f["path"] for f in data["files"]}
    assert "README.md" in paths
    assert "api/payments.md" in paths


@pytest.mark.asyncio
async def test_docs_workspace_read_file(mock_docs_repo: Path) -> None:
    server = create_docs_workspace_server(mock_docs_repo)

    # Full read
    res = await call_mcp_tool(server, "read_doc_file", {"file_path": "README.md"})
    assert "# API Documentation" in res["content"]

    # Range read
    res_range = await call_mcp_tool(server, "read_doc_file", {"file_path": "README.md", "start_line": 1, "end_line": 1})
    assert res_range["content"] == "# API Documentation"


@pytest.mark.asyncio
async def test_docs_workspace_edit_file_exact_match(mock_docs_repo: Path) -> None:
    server = create_docs_workspace_server(mock_docs_repo)

    target = 'Call `process_payment(amount, currency="USD")` to process transactions.'
    replacement = 'Call `process_payment(amount, currency="USD", auth_token=None)` to process transactions.'

    res = await call_mcp_tool(
        server,
        "edit_doc_file",
        {"file_path": "README.md", "target_block": target, "replacement_block": replacement},
    )
    assert res["success"] is True

    # Verify content on disk was actually modified
    readme_content = (mock_docs_repo / "README.md").read_text(encoding="utf-8")
    assert replacement in readme_content


@pytest.mark.asyncio
async def test_docs_workspace_edit_file_crlf_normalization(mock_docs_repo: Path) -> None:
    """Verify that Windows CRLF in the file still matches LF in the agent edit tool."""
    server = create_docs_workspace_server(mock_docs_repo)
    crlf_file = mock_docs_repo / "crlf_doc.md"
    crlf_file.write_bytes(b"# Windows Doc\r\n\r\nLine 1\r\nLine 2\r\n")

    res = await call_mcp_tool(
        server,
        "edit_doc_file",
        {
            "file_path": "crlf_doc.md",
            "target_block": "Line 1\nLine 2",
            "replacement_block": "Line 1 Modified\nLine 2 Modified",
        },
    )
    assert res["success"] is True
    assert "Line 1 Modified" in crlf_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_docs_workspace_edit_file_ambiguous_and_missing(mock_docs_repo: Path) -> None:
    server = create_docs_workspace_server(mock_docs_repo)

    # Missing target block
    res_missing = await call_mcp_tool(
        server,
        "edit_doc_file",
        {"file_path": "README.md", "target_block": "non-existent text", "replacement_block": "new"},
    )
    assert res_missing["success"] is False
    assert "target_block not found" in res_missing["error"]

    # Duplicate / ambiguous target block
    dup_file = mock_docs_repo / "duplicate.md"
    dup_file.write_text("Hello\nHello\n", encoding="utf-8")
    res_dup = await call_mcp_tool(
        server,
        "edit_doc_file",
        {"file_path": "duplicate.md", "target_block": "Hello", "replacement_block": "Hi"},
    )
    assert res_dup["success"] is False
    assert "found 2 times" in res_dup["error"]


@pytest.mark.asyncio
async def test_docs_workspace_create_file(mock_docs_repo: Path) -> None:
    server = create_docs_workspace_server(mock_docs_repo)

    res = await call_mcp_tool(
        server,
        "create_doc_file",
        {
            "file_path": "guides/getting_started.md",
            "content": "# Getting Started Guide\nStep 1: Install AutoDocs.",
        },
    )
    assert res["success"] is True

    created_path = mock_docs_repo / "guides" / "getting_started.md"
    assert created_path.exists()
    assert "# Getting Started Guide" in created_path.read_text(encoding="utf-8")

    # Error on overwrite=False when file exists
    res_exists = await call_mcp_tool(
        server,
        "create_doc_file",
        {
            "file_path": "guides/getting_started.md",
            "content": "different content",
            "overwrite": False,
        },
    )
    assert res_exists["success"] is False
    assert "already exists" in res_exists["error"]


@pytest.mark.asyncio
async def test_docs_workspace_path_traversal_protection(mock_docs_repo: Path) -> None:
    server = create_docs_workspace_server(mock_docs_repo)

    res_read = await call_mcp_tool(server, "read_doc_file", {"file_path": "../../secret.txt"})
    assert "error" in res_read
    assert "escapes workspace" in res_read["error"]

    res_edit = await call_mcp_tool(
        server,
        "edit_doc_file",
        {"file_path": "../secret.txt", "target_block": "a", "replacement_block": "b"},
    )
    assert res_edit["success"] is False
    assert "escapes workspace" in res_edit["error"]
