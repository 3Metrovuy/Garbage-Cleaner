import hashlib
import logging
from pathlib import Path

LARGE_FILE_THRESHOLD = 1 * 1024 ** 3  # 1 GB


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(target: Path) -> dict[str, dict]:
    """
    Walk target directory and return metadata keyed by absolute path string.
    Large files (>1 GB) are flagged but not hashed. Duplicates are detected
    by hashing only files that share an identical size.
    """
    log = logging.getLogger(__name__)
    results: dict[str, dict] = {}

    # First pass: collect metadata for every file
    size_groups: dict[int, list[str]] = {}  # size_bytes -> [path_str, ...]

    for path in target.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue

        try:
            stat = path.stat()
        except PermissionError:
            log.warning("Permission denied, skipping: %s", path)
            continue

        size = stat.st_size
        entry = {
            "name": path.name,
            "extension": path.suffix.lower(),
            "size_bytes": size,
            "modified_date": stat.st_mtime,
            "accessed_date": stat.st_atime,
            "created_date": stat.st_ctime,
            "content_hash": None,
            "is_large": size > LARGE_FILE_THRESHOLD,
        }
        path_str = str(path.resolve())
        results[path_str] = entry

        if not entry["is_large"]:
            size_groups.setdefault(size, []).append(path_str)

    # Second pass: hash only files that share a size (duplicate candidates)
    for size, paths in size_groups.items():
        if len(paths) < 2:
            continue
        for path_str in paths:
            try:
                results[path_str]["content_hash"] = _hash_file(Path(path_str))
            except (PermissionError, OSError) as exc:
                log.warning("Could not hash %s: %s", path_str, exc)

    return results
