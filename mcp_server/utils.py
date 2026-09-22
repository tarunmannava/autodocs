from pathlib import Path


def resolve_safe_path(base_dir: Path | str, untrusted_relative_path: str) -> Path:
    """
    Resolves an untrusted relative path against base_dir and guarantees
    that the resolved path does not escape base_dir (preventing path traversal).

    Args:
        base_dir: Canonical root directory.
        untrusted_relative_path: Relative path supplied by client/agent.

    Returns:
        Path: Absolute resolved path guaranteed to be within base_dir.

    Raises:
        PermissionError: If the resolved path attempts to escape base_dir.
    """
    base = Path(base_dir).resolve()
    # Strip leading slash/backslash so path is treated strictly relative to base
    clean_path = untrusted_relative_path.lstrip("/\\")
    resolved = (base / clean_path).resolve()

    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise PermissionError(f"Access denied: path escapes workspace: '{untrusted_relative_path}'") from exc

    return resolved


def normalize_newlines(text: str) -> str:
    """
    Normalizes all newline variations (Windows CRLF, old Mac CR) to standard LF.

    Args:
        text: Input text.

    Returns:
        str: Text with standard \\n line endings.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")
