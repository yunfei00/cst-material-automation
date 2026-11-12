"""P3-B: build an offline index of external CST .mtd files, without importing them.

No assumptions are made about the proprietary MTD payload or material
properties. Paths, hashes and filename-derived candidate names are local
metadata only, never material definitions verified by CST.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class MTDEntry:
    relative_path: str
    filename: str
    candidate_name: str
    name_verified: bool
    properties_verified: bool
    size_bytes: int
    modified_utc: str
    sha256: str


@dataclass(frozen=True)
class MTDCatalog:
    root: str
    entries: tuple[MTDEntry, ...]
    duplicate_candidate_names: dict[str, list[str]]
    skipped_symlinks: int


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def scan_mtd_library(root: Path) -> MTDCatalog:
    """Recursively index only .mtd files. No file content is decoded or changed."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)
    entries: list[MTDEntry] = []
    skipped_symlinks = 0
    for directory, dirs, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        safe_dirs = []
        for directory_name in dirs:
            if (current / directory_name).is_symlink():
                skipped_symlinks += 1
            else:
                safe_dirs.append(directory_name)
        dirs[:] = safe_dirs
        for filename in filenames:
            item = current / filename
            if item.is_symlink():
                skipped_symlinks += 1
                continue
            if item.suffix.casefold() != ".mtd":
                continue
            stat = item.stat()
            entries.append(
                MTDEntry(
                    relative_path=item.relative_to(root).as_posix(),
                    filename=filename,
                    candidate_name=item.stem,
                    name_verified=False,
                    properties_verified=False,
                    size_bytes=stat.st_size,
                    modified_utc=datetime.fromtimestamp(
                        stat.st_mtime, tz=UTC
                    ).isoformat(),
                    sha256=_hash_file(item),
                )
            )
    if not entries:
        raise RuntimeError("指定目录及其子目录中没有找到 .mtd 材料文件")
    entries.sort(key=lambda item: item.relative_path.casefold())
    candidates: dict[str, list[str]] = {}
    for entry in entries:
        candidates.setdefault(entry.candidate_name.casefold(), []).append(
            entry.relative_path
        )
    duplicates = {
        name: paths for name, paths in candidates.items() if len(paths) > 1
    }
    return MTDCatalog(
        root=str(root),
        entries=tuple(entries),
        duplicate_candidate_names=duplicates,
        skipped_symlinks=skipped_symlinks,
    )
