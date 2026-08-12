from dataclasses import dataclass

from unidiff import PatchSet


@dataclass(frozen=True)
class ChangedFile:
    path: str
    added_lines: int
    removed_lines: int
    is_added: bool
    is_deleted: bool


def parse_unified_diff(diff: str) -> list[ChangedFile]:
    return [
        ChangedFile(
            path=patch.path,
            added_lines=patch.added,
            removed_lines=patch.removed,
            is_added=patch.is_added_file,
            is_deleted=patch.is_removed_file,
        )
        for patch in PatchSet(diff)
    ]
