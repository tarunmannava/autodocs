from dataclasses import asdict, dataclass
from typing import Any

from unidiff import PatchSet

IGNORED_FILENAMES = {
    "package-lock.json",
    "poetry.lock",
    "yarn.lock",
    "Cargo.lock",
    "Pipfile.lock",
    "pnpm-lock.yaml",
    "go.sum",
    "composer.lock",
}

IGNORED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".7z",
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin",
    ".woff", ".woff2", ".ttf", ".eot"
}


@dataclass
class DiffHunk:
    source_start: int
    source_length: int
    target_start: int
    target_length: int
    section_header: str
    patch_text: str


@dataclass
class ChangedFile:
    path: str
    added_lines: int
    removed_lines: int
    is_added: bool
    is_deleted: bool
    hunks: list[DiffHunk]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_ignored_file(file_path: str) -> bool:
    filename = file_path.split("/")[-1].split("\\")[-1]
    if filename in IGNORED_FILENAMES:
        return True
    
    ext = "." + filename.split(".")[-1] if "." in filename else ""
    if ext.lower() in IGNORED_EXTENSIONS:
        return True

    return False


def parse_unified_diff(diff_text: str, filter_ignored: bool = True) -> list[ChangedFile]:
    """
    Parses unified git diff text using unidiff into structured ChangedFile objects.

    Args:
        diff_text: Raw unified diff string.
        filter_ignored: If True, automatically filters out lockfiles and binary extensions.

    Returns:
        List of ChangedFile dataclass instances.
    """
    if not diff_text.strip():
        return []

    patch_set = PatchSet(diff_text)
    results: list[ChangedFile] = []

    for patched_file in patch_set:
        file_path = patched_file.path
        if filter_ignored and is_ignored_file(file_path):
            continue

        hunks: list[DiffHunk] = []
        for hunk in patched_file:
            hunks.append(
                DiffHunk(
                    source_start=hunk.source_start,
                    source_length=hunk.source_length,
                    target_start=hunk.target_start,
                    target_length=hunk.target_length,
                    section_header=hunk.section_header or "",
                    patch_text=str(hunk),
                )
            )

        results.append(
            ChangedFile(
                path=file_path,
                added_lines=patched_file.added,
                removed_lines=patched_file.removed,
                is_added=patched_file.is_added_file,
                is_deleted=patched_file.is_removed_file,
                hunks=hunks,
            )
        )

    return results
