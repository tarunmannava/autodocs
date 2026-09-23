from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field


class InlineStyleChunk(BaseModel):
    """Represents an inline styled span (e.g. bold or code) within a text block."""

    model_config = ConfigDict(extra="ignore")

    start: int
    end: int
    bold: bool = False
    code: bool = False
    link_url: Optional[str] = None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class MarkdownTextBlock(BaseModel):
    """Represents a formatted text block (paragraph, heading, or code block)."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["text_block"] = "text_block"
    clean_text: str
    style: Literal["NORMAL_TEXT", "HEADING_1", "HEADING_2", "HEADING_3", "HEADING_4"] = "NORMAL_TEXT"
    is_bullet: bool = False
    is_code_block: bool = False
    chunks: List[InlineStyleChunk] = Field(default_factory=list)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class MarkdownTableBlock(BaseModel):
    """Represents a markdown table with column headers and cell rows."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["table"] = "table"
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


MarkdownBlock = Union[MarkdownTextBlock, MarkdownTableBlock]


class MarkdownDocumentSection(BaseModel):
    """Represents a top-level or second-level section of a markdown document."""

    model_config = ConfigDict(extra="ignore")

    heading: str
    level: int = 2
    raw_markdown: str = ""
    blocks: List[Dict[str, Any]] = Field(default_factory=list)


class MarkdownDocument(BaseModel):
    """Full structured representation of a Markdown document with parsed sections."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    sections: List[MarkdownDocumentSection] = Field(default_factory=list)
    total_blocks: int = 0


class DocumentEditProposal(BaseModel):
    """Structured proposal for in-place line or block modification."""

    model_config = ConfigDict(extra="ignore")

    file_path: Optional[str] = None
    target_block: str
    replacement_block: str
    rationale: Optional[str] = None


class DocumentInsertProposal(BaseModel):
    """Structured proposal for inserting new documentation between existing lines."""

    model_config = ConfigDict(extra="ignore")

    anchor_text: str
    content_to_insert: str
    position: Literal["after", "before"] = "after"
    rationale: Optional[str] = None


class DocumentSectionPatchProposal(BaseModel):
    """Structured proposal for patching a specific section under a heading."""

    model_config = ConfigDict(extra="ignore")

    heading_title: str
    markdown_content: str
    document_id: Optional[str] = None
    rationale: Optional[str] = None


class GoogleDocSyncResult(BaseModel):
    """Structured result from a Google Doc sync, edit, or insert operation."""

    model_config = ConfigDict(extra="ignore")

    success: bool = True
    document_id: str
    url: Optional[str] = None
    action: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    modified_range: Optional[Tuple[int, int]] = None
