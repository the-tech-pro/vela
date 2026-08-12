"""Safe exposure of finalized download files to desktop integrations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable


def validated_library_file(file_path: str | os.PathLike[str] | None, library_root: str) -> str | None:
    """Return a canonical completed file only when it is inside Vela's library."""
    if not file_path or not library_root:
        return None
    root = os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(library_root))))
    path = os.path.normcase(
        os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(file_path))))
    )
    if not os.path.isfile(path):
        return None
    try:
        if os.path.commonpath((root, path)) != root or path == root:
            return None
    except ValueError:
        return None
    return path


def completed_library_files(results: Iterable[Any], library_root: str) -> list[str]:
    """Collect distinct successful outputs; skipped/missing/out-of-root rows are omitted."""
    paths: list[str] = []
    seen: set[str] = set()
    for result in results:
        status = getattr(getattr(result, "status", None), "name", "")
        if status != "COMPLETED":
            continue
        path = validated_library_file(getattr(result, "file_path", None), library_root)
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths
