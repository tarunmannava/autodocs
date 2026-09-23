from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from backend.models.markdown import (
    InlineStyleChunk,
    MarkdownBlock,
    MarkdownTableBlock,
    MarkdownTextBlock,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class GoogleDocsService:
    """
    Service for interacting with Google Docs API to convert Markdown into native
    Google Docs rich text (real headings, real tables, inline bold, monospace code, bullets).
    """

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        token_path: str = "token.json",
    ) -> None:
        raw_cred = credentials_path or os.getenv("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
        self.credentials_path = str(raw_cred if Path(raw_cred).is_absolute() else (PROJECT_ROOT / raw_cred))
        self.token_path = str(token_path if Path(token_path).is_absolute() else (PROJECT_ROOT / token_path))
        self._service: Optional[Resource] = None

    def get_service(self) -> Resource:
        """Initializes and returns the authenticated Google Docs API Resource."""
        if self._service is not None:
            return self._service

        creds = None

        # 1. Check for cached token.json
        if Path(self.token_path).exists():
            try:
                creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            except Exception as e:
                logger.warning(f"Failed loading cached token.json: {e}")

        # 2. Check for Service Account JSON
        cred_file = Path(self.credentials_path)
        if creds is None and cred_file.exists():
            try:
                with open(cred_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("type") == "service_account":
                    logger.info(f"Authenticating via Google Service Account: {cred_file}")
                    creds = service_account.Credentials.from_service_account_file(
                        str(cred_file), scopes=SCOPES
                    )
            except Exception as e:
                logger.warning(f"Failed loading service account file: {e}")

        # 3. Refresh or prompt for OAuth2 login if needed
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif creds is None:
            oauth_file = cred_file if cred_file.exists() else Path("client_secrets.json")
            if oauth_file.exists():
                flow = InstalledAppFlow.from_client_secrets_file(str(oauth_file), SCOPES)
                creds = flow.run_local_server(port=0)
                with open(self.token_path, "w", encoding="utf-8") as token:
                    token.write(creds.to_json())
            else:
                raise FileNotFoundError(
                    f"No valid Google credentials found at '{self.credentials_path}' or '{self.token_path}'."
                )

        self._service = build("docs", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def get_document(self, document_id: str) -> Dict[str, Any]:
        """Fetches the raw document structure from Google Docs API."""
        service = self.get_service()
        return service.documents().get(documentId=document_id).execute()

    def clear_document(self, document_id: str) -> None:
        """Clears all content from the Google Document."""
        doc = self.get_document(document_id)
        body = doc.get("body", {})
        content = body.get("content", [])
        if not content:
            return

        end_index = content[-1].get("endIndex", 1)
        if end_index > 2:
            requests = [
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": 1,
                            "endIndex": end_index - 1,
                        }
                    }
                }
            ]
            self.get_service().documents().batchUpdate(
                documentId=document_id, body={"requests": requests}
            ).execute()

    def build_text_map(self, document_id: str) -> Tuple[str, List[Tuple[str, int]], Dict[str, Any]]:
        """
        Traverses the Google Doc AST and constructs a contiguous text string alongside
        a character-by-character mapping from string indices to Google Docs API character offsets.
        Extracts paragraphs across both standard document body and nested table cells.
        """
        doc = self.get_document(document_id)
        char_map: List[Tuple[str, int]] = []
        full_text_chars: List[str] = []

        def extract_content(elements: List[Dict[str, Any]]) -> None:
            for elem in elements:
                if "paragraph" in elem:
                    for pe in elem["paragraph"].get("elements", []):
                        if "textRun" in pe:
                            text = pe["textRun"].get("content", "")
                            start = pe.get("startIndex", 0)
                            utf16_offset = 0
                            for ch in text:
                                char_map.append((ch, start + utf16_offset))
                                full_text_chars.append(ch)
                                utf16_offset += 2 if ord(ch) > 0xFFFF else 1
                elif "table" in elem:
                    for row in elem["table"].get("tableRows", []):
                        for cell in row.get("tableCells", []):
                            extract_content(cell.get("content", []))

        extract_content(doc.get("body", {}).get("content", []))
        return "".join(full_text_chars), char_map, doc

    def find_text_offsets(
        self, document_id: str, query: str, normalize_whitespace: bool = True
    ) -> List[Tuple[int, int]]:
        """
        Finds all UTF-16 character ranges [startIndex, endIndex) of query inside the Google Doc.
        Returns a list of (start_idx, end_idx) tuples.
        """
        full_text, char_map, _ = self.build_text_map(document_id)
        if not query or not full_text:
            return []

        # 1. Direct exact search
        matches: List[Tuple[int, int]] = []
        start_search = 0
        while True:
            idx = full_text.find(query, start_search)
            if idx == -1:
                break
            g_start = char_map[idx][1]
            g_end = char_map[idx + len(query) - 1][1] + 1
            matches.append((g_start, g_end))
            start_search = idx + 1

        if matches:
            return matches

        # 2. Whitespace-tolerant search (trimming trailing whitespace per line)
        if normalize_whitespace and "\n" in query:
            norm_query_lines = [line.rstrip() for line in query.splitlines() if line.strip()]
            doc_lines_with_ends = full_text.splitlines(keepends=True)
            norm_doc_lines = [line.rstrip() for line in full_text.splitlines()]
            q_len = len(norm_query_lines)

            # Precalculate start character offset of each line in full_text
            line_offsets = []
            cur_offset = 0
            for line_str in doc_lines_with_ends:
                line_offsets.append(cur_offset)
                cur_offset += len(line_str)

            for i in range(len(norm_doc_lines) - q_len + 1):
                if norm_doc_lines[i : i + q_len] == norm_query_lines:
                    first_line = norm_query_lines[0]
                    last_line = norm_query_lines[-1]
                    f_line_start = line_offsets[i]
                    f_idx = full_text.find(first_line, f_line_start)
                    l_line_start = line_offsets[i + q_len - 1]
                    l_idx = full_text.find(last_line, l_line_start)
                    if (
                        f_idx != -1
                        and l_idx != -1
                        and f_idx < len(char_map)
                        and (l_idx + len(last_line) - 1) < len(char_map)
                    ):
                        g_start = char_map[f_idx][1]
                        g_end = char_map[l_idx + len(last_line) - 1][1] + 1
                        matches.append((g_start, g_end))

        return matches

    def replace_text_block(
        self,
        document_id: str,
        target_block: str,
        replacement_block: str,
    ) -> Dict[str, Any]:
        """
        Replaces target_block with replacement_block in-place, preserving surrounding content.
        Applies rich formatting (bold, Consolas monospace, headings, bullets) to replacement text.
        """
        matches = self.find_text_offsets(document_id, target_block)
        if len(matches) == 0:
            return {
                "success": False,
                "error": f"target_block not found in Google Doc: '{target_block[:80]}...'",
            }
        if len(matches) > 1:
            return {
                "success": False,
                "error": (
                    f"target_block found {len(matches)} times in Google Doc. "
                    "Provide more surrounding context lines to uniquely identify the section."
                ),
            }

        start_idx, end_idx = matches[0]

        # 1. Delete target range
        delete_req = [
            {
                "deleteContentRange": {
                    "range": {"startIndex": start_idx, "endIndex": end_idx}
                }
            }
        ]
        self.get_service().documents().batchUpdate(
            documentId=document_id, body={"requests": delete_req}
        ).execute()

        # 2. Parse and insert replacement block sections at start_idx
        sections = parse_markdown_to_rich_sections(replacement_block)
        self._insert_sections(document_id, sections, at_index=start_idx)

        return {
            "success": True,
            "document_id": document_id,
            "message": "Successfully replaced text block in-place in Google Doc.",
            "replaced_range": [start_idx, end_idx],
        }

    def insert_at_anchor(
        self,
        document_id: str,
        anchor_text: str,
        content_to_insert: str,
        position: str = "after",
    ) -> Dict[str, Any]:
        """
        Inserts new content directly before or after an anchor text line in Google Doc.
        Preserves all surrounding text and shifts indices cleanly.
        """
        matches = self.find_text_offsets(document_id, anchor_text)
        if len(matches) == 0:
            return {
                "success": False,
                "error": f"Anchor text not found in Google Doc: '{anchor_text[:80]}...'",
            }
        if len(matches) > 1:
            return {
                "success": False,
                "error": f"Anchor text found {len(matches)} times in Google Doc. Provide more unique context.",
            }

        start_idx, end_idx = matches[0]

        full_text, char_map, _ = self.build_text_map(document_id)
        if position.lower() == "before":
            insert_idx = start_idx
        else:
            insert_idx = end_idx
            # If anchor line is followed by a newline, insert right after that newline
            for idx_in_text, (ch, g_idx) in enumerate(char_map):
                if g_idx == end_idx - 1:
                    if idx_in_text + 1 < len(char_map) and char_map[idx_in_text + 1][0] == "\n":
                        insert_idx = char_map[idx_in_text + 1][1] + 1
                    break

        sections = parse_markdown_to_rich_sections(content_to_insert)
        self._insert_sections(document_id, sections, at_index=insert_idx)

        return {
            "success": True,
            "document_id": document_id,
            "insert_index": insert_idx,
            "message": f"Successfully inserted content {position} anchor in Google Doc.",
        }

    def patch_section(
        self,
        document_id: str,
        heading_title: str,
        markdown_content: str,
    ) -> Dict[str, Any]:
        """
        Updates or creates a specific section under a heading (e.g. '## Tasks CRUD').
        Identifies the boundary between this heading and the next heading of equal or higher level,
        replaces that range with updated content, and preserves all other sections.
        """
        doc = self.get_document(document_id)
        content = doc.get("body", {}).get("content", [])

        # Find heading element
        heading_elem_idx = None
        heading_level = 2
        clean_target = heading_title.lstrip("#").strip().lower()

        for idx, elem in enumerate(content):
            p = elem.get("paragraph")
            if not p:
                continue
            style = p.get("paragraphStyle", {}).get("namedStyleType", "")
            if "HEADING" in style:
                text = "".join(pe.get("textRun", {}).get("content", "") for pe in p.get("elements", [])).strip()
                if text.lower() == clean_target:
                    heading_elem_idx = idx
                    suffix = style.split("_")[-1]
                    heading_level = int(suffix) if suffix.isdigit() else 2
                    break

        if heading_elem_idx is None:
            # Heading does not exist; append new section at document end
            current_end = content[-1].get("endIndex", 1) if content else 1
            insert_idx = max(1, current_end - 1)
            sections = parse_markdown_to_rich_sections(f"## {heading_title}\n\n" + markdown_content)
            self._insert_sections(document_id, sections, at_index=insert_idx)
            return {
                "success": True,
                "action": "appended",
                "message": f"Section '{heading_title}' not found; appended at document end.",
            }

        # Find end of this section (start of next heading of equal or higher level, or document end)
        section_start_idx = content[heading_elem_idx].get("endIndex", 1)
        section_end_idx = content[-1].get("endIndex", 1) - 1

        for elem in content[heading_elem_idx + 1 :]:
            p = elem.get("paragraph")
            if not p:
                continue
            style = p.get("paragraphStyle", {}).get("namedStyleType", "")
            if "HEADING" in style:
                suffix = style.split("_")[-1]
                lvl = int(suffix) if suffix.isdigit() else 2
                if lvl <= heading_level:
                    section_end_idx = elem.get("startIndex", section_end_idx)
                    break

        # If there is existing section content to replace
        if section_end_idx > section_start_idx:
            del_req = [{
                "deleteContentRange": {
                    "range": {"startIndex": section_start_idx, "endIndex": section_end_idx}
                }
            }]
            self.get_service().documents().batchUpdate(documentId=document_id, body={"requests": del_req}).execute()

        # Insert new content right after heading
        sections = parse_markdown_to_rich_sections(markdown_content)
        self._insert_sections(document_id, sections, at_index=section_start_idx)

        return {
            "success": True,
            "action": "updated",
            "message": f"Successfully patched section '{heading_title}'.",
        }

    def sync_markdown_to_doc(
        self,
        document_id: str,
        markdown_text: str,
        clear_first: bool = False,
    ) -> str:
        """
        Translates raw Markdown into rich, native Google Docs formatting:
        - If clear_first=True, resets the document and creates all sections from scratch.
        - If clear_first=False, reconciles section-by-section without erasing existing content.
        """
        if clear_first:
            self.clear_document(document_id)
            sections = parse_markdown_to_rich_sections(markdown_text)
            self._insert_sections(document_id, sections)
            doc_url = f"https://docs.google.com/document/d/{document_id}/edit"
            logger.info(f"Successfully rendered rich document into Google Docs: {doc_url}")
            return doc_url

        # Section-by-section non-destructive reconciliation
        lines = markdown_text.splitlines()
        heading_blocks: List[Tuple[str, List[str]]] = []
        current_heading = ""
        current_lines: List[str] = []

        for line in lines:
            if line.startswith("# ") or line.startswith("## "):
                if current_heading or current_lines:
                    heading_blocks.append((current_heading, current_lines))
                current_heading = line.lstrip("#").strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_heading or current_lines:
            heading_blocks.append((current_heading, current_lines))

        for h_title, h_lines in heading_blocks:
            body_md = "\n".join(h_lines).strip()
            if not body_md:
                continue
            if h_title:
                self.patch_section(document_id, h_title, body_md)
            else:
                doc = self.get_document(document_id)
                content = doc.get("body", {}).get("content", [])
                if len(content) <= 1:
                    sections = parse_markdown_to_rich_sections(body_md)
                    self._insert_sections(document_id, sections)

        doc_url = f"https://docs.google.com/document/d/{document_id}/edit"
        logger.info(f"Successfully updated document sections non-destructively: {doc_url}")
        return doc_url

    def _insert_sections(
        self,
        document_id: str,
        sections: List[Dict[str, Any]],
        at_index: Optional[int] = None,
    ) -> int:
        """
        Inserts multiple document sections (paragraphs, headings, code blocks, tables),
        batching consecutive text blocks into unified requests to minimize API round trips,
        and accurately advancing indices across table insertions.
        """
        if not sections:
            return 0

        if at_index is None:
            doc = self.get_document(document_id)
            current_end = doc.get("body", {}).get("content", [])[-1].get("endIndex", 1)
            insert_idx = max(1, current_end - 1)
        else:
            insert_idx = at_index

        total_inserted = 0
        curr_idx = at_index

        # Group sections into contiguous text blocks vs tables
        idx = 0
        while idx < len(sections):
            sec = sections[idx]
            target_idx = curr_idx if at_index is not None else None
            if sec.get("type") == "text_block":
                text_group: List[Dict[str, Any]] = []
                while idx < len(sections) and sections[idx].get("type") == "text_block":
                    text_group.append(sections[idx])
                    idx += 1
                inserted = self._insert_text_blocks_batched(document_id, text_group, at_index=target_idx)
                if curr_idx is not None:
                    curr_idx += inserted
                total_inserted += inserted
            elif sec.get("type") == "table":
                inserted = self._insert_table(
                    document_id, sec.get("headers", []), sec.get("rows", []), at_index=target_idx
                )
                if curr_idx is not None:
                    curr_idx += inserted
                total_inserted += inserted
                idx += 1
            else:
                idx += 1

        return total_inserted

    def _insert_text_blocks_batched(
        self,
        document_id: str,
        sections: List[Dict[str, Any]],
        at_index: Optional[int] = None,
    ) -> int:
        """
        Batches multiple consecutive text blocks into a single batchUpdate API call.
        Properly applies styles (headings, bullets, Consolas for code blocks and inline code).
        """
        if not sections:
            return 0

        service = self.get_service()
        if at_index is None:
            doc = self.get_document(document_id)
            current_end = doc.get("body", {}).get("content", [])[-1].get("endIndex", 1)
            insert_idx = max(1, current_end - 1)
        else:
            insert_idx = at_index

        full_text_parts: List[str] = []
        block_ranges: List[Tuple[int, int, Dict[str, Any]]] = []
        offset = 0

        for sec in sections:
            raw_text = sec["clean_text"] + "\n"
            start_off = offset
            end_off = offset + len(raw_text)
            block_ranges.append((start_off, end_off, sec))
            full_text_parts.append(raw_text)
            offset = end_off

        combined_text = "".join(full_text_parts)
        if not combined_text:
            return 0

        reqs: List[Dict[str, Any]] = [
            {
                "insertText": {
                    "location": {"index": insert_idx},
                    "text": combined_text,
                }
            }
        ]

        for start_off, end_off, sec in block_ranges:
            b_start = insert_idx + start_off
            b_end = insert_idx + end_off
            style = sec.get("style", "NORMAL_TEXT")
            is_bullet = sec.get("is_bullet", False)
            is_code_block = sec.get("is_code_block", False)

            reqs.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": b_start, "endIndex": b_end},
                    "paragraphStyle": {"namedStyleType": style},
                    "fields": "namedStyleType",
                }
            })

            if is_bullet:
                reqs.append({
                    "createParagraphBullets": {
                        "range": {"startIndex": b_start, "endIndex": b_end},
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                })

            if is_code_block:
                reqs.append({
                    "updateTextStyle": {
                        "range": {"startIndex": b_start, "endIndex": b_end},
                        "textStyle": {"weightedFontFamily": {"fontFamily": "Consolas"}},
                        "fields": "weightedFontFamily",
                    }
                })

            for chunk in sec.get("chunks", []):
                chunk_start = b_start + chunk["start"]
                chunk_end = b_start + chunk["end"]
                if chunk_end <= chunk_start:
                    continue

                text_style: Dict[str, Any] = {}
                fields: List[str] = []

                if chunk.get("bold"):
                    text_style["bold"] = True
                    fields.append("bold")
                if chunk.get("code"):
                    text_style["weightedFontFamily"] = {"fontFamily": "Consolas"}
                    fields.append("weightedFontFamily")
                if chunk.get("link_url"):
                    text_style["link"] = {"url": chunk["link_url"]}
                    fields.append("link")

                if fields:
                    reqs.append({
                        "updateTextStyle": {
                            "range": {"startIndex": chunk_start, "endIndex": chunk_end},
                            "textStyle": text_style,
                            "fields": ",".join(fields),
                        }
                    })

        service.documents().batchUpdate(documentId=document_id, body={"requests": reqs}).execute()
        return len(combined_text)

    def _insert_text_block(
        self,
        document_id: str,
        section: Dict[str, Any],
        at_index: Optional[int] = None,
    ) -> int:
        """Inserts formatted paragraphs, headings, or code blocks at the specified index (or end)."""
        return self._insert_text_blocks_batched(document_id, [section], at_index=at_index)

    def _insert_table(
        self,
        document_id: str,
        headers: List[str],
        rows: List[List[str]],
        at_index: Optional[int] = None,
    ) -> int:
        """Creates a real native Google Docs table, populates cells, and returns character span."""
        service = self.get_service()
        if at_index is None:
            doc = self.get_document(document_id)
            current_end = doc.get("body", {}).get("content", [])[-1].get("endIndex", 1)
            insert_idx = max(1, current_end - 1)
        else:
            insert_idx = at_index

        total_rows = 1 + len(rows)
        total_cols = len(headers)
        if total_cols == 0 or total_rows == 0:
            return 0

        # 1. Insert table structure
        insert_table_req = [
            {"insertTable": {"rows": total_rows, "columns": total_cols, "location": {"index": insert_idx}}}
        ]
        service.documents().batchUpdate(documentId=document_id, body={"requests": insert_table_req}).execute()

        # 2. Re-fetch document to locate the new table's cell indices
        updated_doc = self.get_document(document_id)
        table_elem = None
        for elem in updated_doc.get("body", {}).get("content", []):
            if "table" in elem and elem.get("startIndex", 0) >= insert_idx:
                table_elem = elem["table"]
                break

        if not table_elem:
            return 0

        all_data = [headers] + rows
        cell_para_indices = []
        for r in table_elem.get("tableRows", []):
            row_indices = []
            for c in r.get("tableCells", []):
                para = c.get("content", [{}])[0]
                row_indices.append(para.get("startIndex", 0))
            cell_para_indices.append(row_indices)

        # 3. Populate cells in reverse order to keep indices stable
        populate_reqs: List[Dict[str, Any]] = []
        style_reqs: List[Dict[str, Any]] = []
        total_text_len = 0

        for r_idx in reversed(range(min(len(all_data), len(cell_para_indices)))):
            for c_idx in reversed(range(min(len(all_data[r_idx]), len(cell_para_indices[r_idx])))):
                idx = cell_para_indices[r_idx][c_idx]
                val = clean_markdown_inline(all_data[r_idx][c_idx])
                if not val:
                    continue

                populate_reqs.append({
                    "insertText": {"location": {"index": idx}, "text": val}
                })
                total_text_len += len(val)

                # Bold the header row cells
                if r_idx == 0:
                    style_reqs.append({
                        "updateTextStyle": {
                            "range": {"startIndex": idx, "endIndex": idx + len(val)},
                            "textStyle": {"bold": True},
                            "fields": "bold",
                        }
                    })

        if populate_reqs:
            service.documents().batchUpdate(documentId=document_id, body={"requests": populate_reqs}).execute()

        if style_reqs:
            service.documents().batchUpdate(documentId=document_id, body={"requests": style_reqs}).execute()

        final_doc = self.get_document(document_id)
        for elem in final_doc.get("body", {}).get("content", []):
            if "table" in elem and elem.get("startIndex", 0) >= insert_idx:
                return elem.get("endIndex", insert_idx) - insert_idx
        return total_text_len


def clean_markdown_inline(text: str) -> str:
    """Strips markdown inline markers (**, `, *, links) leaving clean readable text."""
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r"\1", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    return text.strip()


def parse_inline_styles(text: str) -> Tuple[str, List[InlineStyleChunk]]:
    """
    Parses a string containing **bold**, `code`, and [links](url) into clean text and style range chunks.
    Returns (clean_text, list of InlineStyleChunk models).
    """
    pattern = re.compile(r"(\*\*(.*?)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?://[^\s\)]+)\))")
    chunks: List[InlineStyleChunk] = []
    clean_parts: List[str] = []
    last_end = 0
    clean_pos = 0

    for match in pattern.finditer(text):
        start, end = match.span()
        if start > last_end:
            plain = text[last_end:start]
            clean_parts.append(plain)
            clean_pos += len(plain)

        _, bold_inner, code_inner, link_text, link_url = (
            match.group(1),
            match.group(2),
            match.group(3),
            match.group(4),
            match.group(5),
        )
        if bold_inner is not None:
            chunk_len = len(bold_inner)
            chunks.append(InlineStyleChunk(start=clean_pos, end=clean_pos + chunk_len, bold=True, code=False))
            clean_parts.append(bold_inner)
            clean_pos += chunk_len
        elif code_inner is not None:
            chunk_len = len(code_inner)
            chunks.append(InlineStyleChunk(start=clean_pos, end=clean_pos + chunk_len, bold=False, code=True))
            clean_parts.append(code_inner)
            clean_pos += chunk_len
        elif link_text is not None and link_url is not None:
            chunk_len = len(link_text)
            chunks.append(
                InlineStyleChunk(
                    start=clean_pos,
                    end=clean_pos + chunk_len,
                    bold=False,
                    code=False,
                    link_url=link_url,
                )
            )
            clean_parts.append(link_text)
            clean_pos += chunk_len

        last_end = end

    if last_end < len(text):
        tail = text[last_end:]
        clean_parts.append(tail)

    return "".join(clean_parts), chunks


def parse_markdown_blocks(markdown_text: str) -> List[MarkdownBlock]:
    """
    Splits markdown into strongly-typed Pydantic MarkdownBlock instances:
    - Headings (HEADING_1, HEADING_2, HEADING_3, HEADING_4)
    - Formatted paragraphs
    - Bullet items
    - Monospace code blocks
    - Tables
    """
    lines = markdown_text.splitlines()
    blocks: List[MarkdownBlock] = []

    in_code_block = False
    code_block_lines: List[str] = []

    in_table = False
    table_headers: List[str] = []
    table_rows: List[List[str]] = []

    for line in lines:
        stripped = line.strip()

        # Handle Fenced Code Blocks
        if stripped.startswith("```"):
            if in_code_block:
                in_code_block = False
                code_text = "\n".join(code_block_lines)
                blocks.append(
                    MarkdownTextBlock(
                        clean_text=code_text,
                        style="NORMAL_TEXT",
                        is_code_block=True,
                        chunks=[],
                    )
                )
                code_block_lines = []
            else:
                in_code_block = True
                code_block_lines = []
            continue

        if in_code_block:
            code_block_lines.append(line)
            continue

        # Handle Tables
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # Check if separator row like |---|---|
            if all(set(c).issubset({"-", ":", " "}) for c in cells if c):
                continue
            if not in_table:
                in_table = True
                table_headers = cells
                table_rows = []
            else:
                table_rows.append(cells)
            continue
        else:
            if in_table:
                blocks.append(MarkdownTableBlock(headers=table_headers, rows=table_rows))
                in_table = False
                table_headers = []
                table_rows = []

        # Ignore horizontal dividers
        if stripped in {"---", "***", "___"}:
            continue

        # Empty lines
        if not stripped:
            continue

        # Headings
        if stripped.startswith("# "):
            clean, chunks = parse_inline_styles(stripped[2:])
            blocks.append(MarkdownTextBlock(clean_text=clean, style="HEADING_1", chunks=chunks))
        elif stripped.startswith("## "):
            clean, chunks = parse_inline_styles(stripped[3:])
            blocks.append(MarkdownTextBlock(clean_text=clean, style="HEADING_2", chunks=chunks))
        elif stripped.startswith("### "):
            clean, chunks = parse_inline_styles(stripped[4:])
            blocks.append(MarkdownTextBlock(clean_text=clean, style="HEADING_3", chunks=chunks))
        elif stripped.startswith("#### "):
            clean, chunks = parse_inline_styles(stripped[5:])
            blocks.append(MarkdownTextBlock(clean_text=clean, style="HEADING_4", chunks=chunks))
        elif stripped.startswith("* ") or stripped.startswith("- "):
            clean, chunks = parse_inline_styles(stripped[2:])
            blocks.append(
                MarkdownTextBlock(
                    clean_text=clean,
                    style="NORMAL_TEXT",
                    is_bullet=True,
                    chunks=chunks,
                )
            )
        else:
            clean, chunks = parse_inline_styles(line)
            blocks.append(MarkdownTextBlock(clean_text=clean, style="NORMAL_TEXT", chunks=chunks))

    # Flush any remaining table
    if in_table:
        blocks.append(MarkdownTableBlock(headers=table_headers, rows=table_rows))

    return blocks


def parse_markdown_to_rich_sections(markdown_text: str) -> List[Dict[str, Any]]:
    """
    Splits markdown into structured document sections validated through Pydantic models.
    Returns a list of dictionary representations suitable for Google Docs API operations.
    """
    blocks = parse_markdown_blocks(markdown_text)
    return [b.to_dict() for b in blocks]

