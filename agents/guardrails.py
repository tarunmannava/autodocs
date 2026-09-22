from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


@dataclass
class GuardrailResult:
    """Represents the outcome of post-edit documentation verification."""

    passed: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def validate_markdown_syntax(content: str) -> List[str]:
    """
    Checks for common Markdown syntax errors such as unclosed code fences.

    Args:
        content: Raw markdown text.

    Returns:
        List[str]: List of syntax errors discovered.
    """
    errors: List[str] = []
    lines = content.splitlines()

    fence_count = sum(1 for line in lines if line.strip().startswith("```"))
    if fence_count % 2 != 0:
        errors.append(f"Unclosed code fence detected (found {fence_count} '```' fence markers)")

    return errors


def validate_relative_links(docs_dir: Path, file_path: str, content: str) -> List[str]:
    """
    Validates that relative markdown file links within content resolve to actual files.

    Args:
        docs_dir: Root directory of the documentation repository.
        file_path: Relative path of the document containing links.
        content: Raw markdown text.

    Returns:
        List[str]: List of broken link errors discovered.
    """
    errors: List[str] = []
    doc_file_path = (docs_dir / file_path).resolve()
    doc_dir = doc_file_path.parent

    for match in MARKDOWN_LINK_PATTERN.finditer(content):
        _, target = match.groups()
        target = target.strip()

        # Skip external URLs, anchors, and mailto links
        if (
            target.startswith(("http://", "https://", "mailto:", "#"))
            or not target
        ):
            continue

        # Strip anchor fragment if present (e.g. "auth.md#login" -> "auth.md")
        clean_target = target.split("#", 1)[0]
        if not clean_target:
            continue

        # Resolve relative target against the document directory
        resolved_link = (doc_dir / clean_target).resolve()

        if not resolved_link.exists():
            errors.append(
                f"Broken relative link in '{file_path}': '{target}' does not exist on disk"
            )

    return errors


def verify_documentation_edits(
    docs_dir: Path | str,
    modified_files: List[str],
) -> GuardrailResult:
    """
    Executes all deterministic guardrail checks on the files modified by the documentation agent.

    Args:
        docs_dir: Root path of the documentation repository.
        modified_files: List of relative file paths modified or created by the agent.

    Returns:
        GuardrailResult: Validation status and error details.
    """
    docs_root = Path(docs_dir).resolve()
    all_errors: List[str] = []

    if not modified_files:
        return GuardrailResult(passed=True, warnings=["No files were modified by the agent."])

    for rel_path in modified_files:
        target_file = (docs_root / rel_path).resolve()

        if not target_file.exists():
            all_errors.append(f"Modified file does not exist on disk: '{rel_path}'")
            continue

        try:
            content = target_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            all_errors.append(f"Could not read modified file '{rel_path}': {exc}")
            continue

        if not content.strip():
            all_errors.append(f"Modified file is empty: '{rel_path}'")
            continue

        # Syntax checks
        syntax_errs = validate_markdown_syntax(content)
        all_errors.extend(f"[{rel_path}] {err}" for err in syntax_errs)

        # Relative link checks
        link_errs = validate_relative_links(docs_root, rel_path, content)
        all_errors.extend(f"[{rel_path}] {err}" for err in link_errs)

    return GuardrailResult(
        passed=len(all_errors) == 0,
        errors=all_errors,
    )
