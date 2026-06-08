"""File size extraction and sorting utilities."""

import os
from typing import Dict, List, Tuple


def get_size(path: str) -> int:
    """Return size in bytes for a file or total size for a directory (recursive)."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Path not found: {path}")
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for entry in os.scandir(path):
        if entry.is_file(follow_symlinks=False):
            total += entry.stat().st_size
        elif entry.is_dir(follow_symlinks=False):
            total += get_size(entry.path)
    return total


def get_sizes(paths: List[str]) -> Dict[str, int]:
    """Map a list of paths to their sizes. Missing paths are omitted."""
    result = {}
    for path in paths:
        try:
            result[path] = get_size(path)
        except (FileNotFoundError, PermissionError, OSError):
            pass
    return result


def sort_by_size(
    paths: List[str], reverse: bool = True
) -> List[Tuple[str, int]]:
    """Sort paths by size. Default descending (largest first)."""
    sizes = get_sizes(paths)
    return sorted(sizes.items(), key=lambda x: x[1], reverse=reverse)


def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable string (B, KB, MB, GB)."""
    for unit, divisor in [("GB", 1073741824), ("MB", 1048576), ("KB", 1024)]:
        if size_bytes >= divisor:
            return f"{size_bytes / divisor:.2f} {unit}"
    return f"{size_bytes} B"


def display_sizes(
    sorted_items: List[Tuple[str, int]], max_bar_length: int = 40
) -> str:
    """Return a formatted string with size visualization bars.

    sorted_items: list of (path, size_bytes) tuples, typically from sort_by_size
    max_bar_length: max character length of the size bar (default 40)
    """
    if not sorted_items:
        return "(no files)"

    max_size = sorted_items[0][1]
    if max_size == 0:
        max_size = 1

    lines = []
    pad = max(len(os.path.basename(p)) for p, _ in sorted_items)
    for i, (path, size) in enumerate(sorted_items, 1):
        bar_len = int((size / max_size) * max_bar_length)
        bar = "█" * bar_len
        size_str = format_size(size)
        name = os.path.basename(path)
        lines.append(f"{i:2}. {bar:<{max_bar_length}} {size_str:>10}  {name}")
    return "\n".join(lines)
