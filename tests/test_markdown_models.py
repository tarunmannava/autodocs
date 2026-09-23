
from backend.models.markdown import (
    DocumentEditProposal,
    DocumentInsertProposal,
    DocumentSectionPatchProposal,
    GoogleDocSyncResult,
    InlineStyleChunk,
    MarkdownTableBlock,
    MarkdownTextBlock,
)
from backend.services.google_docs import parse_inline_styles, parse_markdown_blocks, parse_markdown_to_rich_sections


def test_inline_style_chunk_model() -> None:
    chunk = InlineStyleChunk(start=0, end=5, bold=True, code=False)
    assert chunk.start == 0
    assert chunk.end == 5
    assert chunk.bold is True
    assert chunk.code is False
    # Test subscript and get access
    assert chunk["start"] == 0
    assert chunk.get("bold") is True
    assert chunk.get("nonexistent", "default") == "default"


def test_markdown_text_block_model() -> None:
    chunk = InlineStyleChunk(start=0, end=4, bold=True)
    block = MarkdownTextBlock(
        clean_text="Test Heading",
        style="HEADING_1",
        chunks=[chunk],
    )
    assert block.type == "text_block"
    assert block.style == "HEADING_1"
    assert len(block.chunks) == 1
    assert block["clean_text"] == "Test Heading"

    dumped = block.to_dict()
    assert dumped["type"] == "text_block"
    assert dumped["style"] == "HEADING_1"
    assert dumped["chunks"][0]["bold"] is True


def test_markdown_table_block_model() -> None:
    table = MarkdownTableBlock(
        headers=["Param", "Type", "Description"],
        rows=[["has_subtasks", "boolean", "Filter tasks with subtasks"]],
    )
    assert table.type == "table"
    assert len(table.headers) == 3
    assert len(table.rows) == 1
    assert table["headers"][0] == "Param"

    dumped = table.to_dict()
    assert dumped["headers"] == ["Param", "Type", "Description"]


def test_proposals_models() -> None:
    edit_prop = DocumentEditProposal(
        file_path="README.md",
        target_block="- `priority`: Filter tasks",
        replacement_block="- `priority`: Filter tasks\n- `has_subtasks`: Filter tasks with subtasks",
        rationale="Added query parameter",
    )
    assert edit_prop.file_path == "README.md"
    assert "has_subtasks" in edit_prop.replacement_block

    insert_prop = DocumentInsertProposal(
        anchor_text="`priority` (str): Filter by priority",
        content_to_insert="* `has_subtasks` (bool): Filter tasks that have subtasks",
        position="after",
        rationale="New query filter added to endpoint",
    )
    assert insert_prop.position == "after"
    assert "has_subtasks" in insert_prop.content_to_insert

    patch_prop = DocumentSectionPatchProposal(
        heading_title="API Endpoints",
        markdown_content="### GET /api/tasks\nReturns list of tasks.",
    )
    assert patch_prop.heading_title == "API Endpoints"

    sync_result = GoogleDocSyncResult(
        success=True,
        document_id="doc123",
        action="inserted",
        url="https://docs.google.com/document/d/doc123/edit",
        modified_range=(150, 210),
    )
    assert sync_result.success is True
    assert sync_result.modified_range == (150, 210)


def test_parse_inline_styles() -> None:
    text = "Here is **bold text** and `inline_code` in a line."
    clean, chunks = parse_inline_styles(text)
    assert clean == "Here is bold text and inline_code in a line."
    assert len(chunks) == 2
    assert chunks[0].bold is True
    assert chunks[0].code is False
    assert chunks[1].bold is False
    assert chunks[1].code is True


def test_parse_markdown_blocks_integration() -> None:
    sample_md = """# My Project
A description paragraph with **bold** text.

## API Endpoints
* `GET /api/tasks` - List tasks
* `POST /api/tasks` - Create task

```python
def example():
    return True
```

| Method | Endpoint | Description |
|---|---|---|
| GET | /api/tasks | List all |
| POST | /api/tasks | Create new |
"""
    blocks = parse_markdown_blocks(sample_md)
    assert len(blocks) >= 6

    # Heading 1
    h1 = blocks[0]
    assert isinstance(h1, MarkdownTextBlock)
    assert h1.style == "HEADING_1"
    assert h1.clean_text == "My Project"

    # Paragraph with inline style
    para = blocks[1]
    assert isinstance(para, MarkdownTextBlock)
    assert para.style == "NORMAL_TEXT"
    assert any(c.bold for c in para.chunks)

    # Heading 2
    h2 = blocks[2]
    assert isinstance(h2, MarkdownTextBlock)
    assert h2.style == "HEADING_2"

    # Bullets
    b1 = blocks[3]
    assert isinstance(b1, MarkdownTextBlock)
    assert b1.is_bullet is True

    # Code block
    code = blocks[5]
    assert isinstance(code, MarkdownTextBlock)
    assert code.is_code_block is True
    assert "def example():" in code.clean_text

    # Table block
    table = blocks[6]
    assert isinstance(table, MarkdownTableBlock)
    assert table.headers == ["Method", "Endpoint", "Description"]
    assert len(table.rows) == 2
    assert table.rows[0] == ["GET", "/api/tasks", "List all"]

    # parse_markdown_to_rich_sections should produce list of dicts
    dict_sections = parse_markdown_to_rich_sections(sample_md)
    assert isinstance(dict_sections, list)
    assert dict_sections[0]["clean_text"] == "My Project"
    assert dict_sections[0]["style"] == "HEADING_1"


def test_parse_inline_styles_with_links() -> None:
    from backend.services.google_docs import clean_markdown_inline

    text = "Visit [AutoDocs](https://github.com/autodocs) for **awesome** `code`."
    clean, chunks = parse_inline_styles(text)
    assert clean == "Visit AutoDocs for awesome code."
    assert len(chunks) == 3

    # Link chunk
    assert chunks[0].link_url == "https://github.com/autodocs"
    assert chunks[0].start == 6
    assert chunks[0].end == 14

    # Bold chunk
    assert chunks[1].bold is True
    assert chunks[1].start == 19
    assert chunks[1].end == 26

    # Code chunk
    assert chunks[2].code is True
    assert chunks[2].start == 27
    assert chunks[2].end == 31

    # clean_markdown_inline
    assert clean_markdown_inline(text) == "Visit AutoDocs for awesome code."


def test_google_docs_service_surrogate_pairs() -> None:
    from unittest.mock import MagicMock

    from backend.services.google_docs import GoogleDocsService

    service = GoogleDocsService()
    service.get_document = MagicMock(return_value={
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {
                                "startIndex": 10,
                                "textRun": {"content": "Hello 🚀 World\n"}
                            }
                        ]
                    }
                }
            ]
        }
    })

    full_text, char_map, _ = service.build_text_map("dummy_doc_id")
    assert full_text == "Hello 🚀 World\n"
    # 'H' is at index 10
    assert char_map[0] == ("H", 10)
    # '🚀' is at index 16
    assert char_map[6] == ("🚀", 16)
    # ' ' after emoji should be at 16 + 2 = 18 in UTF-16!
    assert char_map[7] == (" ", 18)
    # 'W' should be at 19
    assert char_map[8] == ("W", 19)


def test_google_docs_service_batched_insert() -> None:
    from unittest.mock import MagicMock

    from backend.services.google_docs import GoogleDocsService

    service = GoogleDocsService()
    mock_service_res = MagicMock()
    mock_batch = MagicMock()
    mock_service_res.documents.return_value.batchUpdate.return_value = mock_batch
    service._service = mock_service_res

    sections = [
        {"type": "text_block", "clean_text": "Heading Title", "style": "HEADING_1", "chunks": []},
        {
            "type": "text_block",
            "clean_text": "Check [Doc](https://example.com) for details.",
            "style": "NORMAL_TEXT",
            "chunks": [{"start": 6, "end": 9, "bold": False, "code": False, "link_url": "https://example.com"}],
        },
        {
            "type": "text_block",
            "clean_text": "def test():\n    pass",
            "style": "NORMAL_TEXT",
            "is_code_block": True,
            "chunks": [],
        },
    ]

    total = service._insert_text_blocks_batched("dummy_doc", sections, at_index=1)
    assert total > 0

    # Ensure batchUpdate was called exactly once for all 3 text blocks
    mock_service_res.documents.return_value.batchUpdate.assert_called_once()
    call_args = mock_service_res.documents.return_value.batchUpdate.call_args[1]
    requests = call_args["body"]["requests"]

    # First request is insertText combining all blocks
    assert "insertText" in requests[0]
    combined = requests[0]["insertText"]["text"]
    assert "Heading Title\n" in combined
    assert "def test():\n    pass\n" in combined

    # Check Consolas style was added for code block
    consolas_req = [
        r for r in requests
        if (
            r.get("updateTextStyle", {})
            .get("textStyle", {})
            .get("weightedFontFamily", {})
            .get("fontFamily")
            == "Consolas"
        )
    ]
    assert len(consolas_req) >= 1

    # Check link was added
    link_req = [
        r for r in requests
        if r.get("updateTextStyle", {}).get("textStyle", {}).get("link", {}).get("url") == "https://example.com"
    ]
    assert len(link_req) == 1
