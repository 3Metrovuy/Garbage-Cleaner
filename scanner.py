import hashlib
import logging
import os
from collections import Counter
from pathlib import Path

LARGE_FILE_THRESHOLD = 1 * 1024 ** 3  # 1 GB

# Names that, when found as a direct child of a folder, tell us what that
# folder "is about".  Checked against both file names and subfolder names.
MARKERS = {
    ".git", "package.json", "package-lock.json", "yarn.lock",
    "node_modules", "venv", ".venv", "__pycache__", ".cache",
    "requirements.txt", "Pipfile", "pyproject.toml",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "Makefile", "Dockerfile", "docker-compose.yml", ".env",
}


def scan(target: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """
    Walk target and return two inventories:
      folders  — path_str -> folder descriptor (every subdirectory)
      files    — path_str -> file descriptor   (every loose file found)

    Symlinks are never followed.  Permission errors are logged and skipped.
    Content-hashes are computed only for PDFs that share an identical size
    (the only items eligible for exact-duplicate auto-deletion).
    """
    log = logging.getLogger(__name__)
    target = target.resolve()

    folder_inventory: dict[str, dict] = {}
    file_inventory: dict[str, dict] = {}

    def _on_error(exc: OSError) -> None:
        log.warning("Skipping inaccessible path: %s", exc)

    # topdown=False means we visit deepest directories first.
    # When we process a parent folder, its children are already in
    # folder_inventory, so we can add their stats to the parent's totals.
    for dirpath_str, dirnames, filenames in os.walk(
        str(target), topdown=False, followlinks=False, onerror=_on_error
    ):
        dirpath = Path(dirpath_str)

        # Drop symlinked subdirectories from the traversal list in-place.
        # os.walk uses this list to decide which dirs to descend into.
        dirnames[:] = [d for d in dirnames if not (dirpath / d).is_symlink()]

        # ── Accumulate stats for files that live directly in this folder ──
        direct_size = 0
        direct_file_count = 0
        ext_counter: Counter = Counter()
        max_mtime = 0.0
        markers_found: set[str] = set()

        for fname in filenames:
            fpath = dirpath / fname
            if fpath.is_symlink():
                continue
            try:
                st = fpath.stat()
            except (PermissionError, OSError) as exc:
                log.warning("Skipping %s: %s", fpath, exc)
                continue

            direct_size += st.st_size
            direct_file_count += 1
            ext = fpath.suffix.lower()
            if ext:
                ext_counter[ext] += 1
            max_mtime = max(max_mtime, st.st_mtime)

            if fname in MARKERS:
                markers_found.add(fname)

            # Collect every loose file into its own inventory.  Whether it is
            # actually offered for review (vs. covered by a parent folder) is
            # decided later in build_report.
            file_inventory[str(fpath)] = {
                "name": fname,
                "ext": ext,
                "size_bytes": st.st_size,
                "modified_date": st.st_mtime,
                "created_date": st.st_ctime,
                "content_hash": None,
                "is_large": st.st_size > LARGE_FILE_THRESHOLD,
            }

        # Check direct subdirectory names for markers
        for dname in dirnames:
            if dname in MARKERS:
                markers_found.add(dname)

        # ── Roll up stats from already-processed child directories ──
        total_size = direct_size
        total_file_count = direct_file_count
        total_subfolder_count = len(dirnames)  # start with direct count

        for dname in dirnames:
            child_key = str(dirpath / dname)
            child = folder_inventory.get(child_key)
            if child is None:
                continue  # inaccessible child — was skipped
            total_size += child["total_size"]
            total_file_count += child["file_count"]
            total_subfolder_count += child["subfolder_count"]
            ext_counter.update(child["_ext_counter"])
            max_mtime = max(max_mtime, child["last_modified"])

        # Depth 0 = target itself; depth 1 = its immediate children; etc.
        try:
            depth = len(dirpath.relative_to(target).parts)
        except ValueError:
            depth = 0

        folder_inventory[str(dirpath)] = {
            "name": dirpath.name if dirpath.name else str(dirpath),
            "path": str(dirpath),
            "depth": depth,
            "total_size": total_size,
            "file_count": total_file_count,
            "subfolder_count": total_subfolder_count,
            "last_modified": max_mtime,
            "dominant_file_types": dict(ext_counter.most_common(5)),
            "markers": sorted(markers_found),
            # Internal field used only during this walk so parents can
            # inherit children's extension counts.  Stripped before return.
            "_ext_counter": ext_counter,
        }

    # ── Remove the internal field now that the walk is complete ──
    for entry in folder_inventory.values():
        entry.pop("_ext_counter", None)

    # ── Hash only PDFs that share an identical size ──
    # Exact-duplicate auto-deletion is restricted to PDFs (see rules.py), and
    # files with unique sizes cannot be duplicates, so we hash nothing else.
    size_groups: dict[int, list[str]] = {}
    for path_str, meta in file_inventory.items():
        if meta["ext"] == ".pdf" and not meta["is_large"]:
            size_groups.setdefault(meta["size_bytes"], []).append(path_str)

    for paths in size_groups.values():
        if len(paths) < 2:
            continue
        for path_str in paths:
            try:
                file_inventory[path_str]["content_hash"] = _hash_file(Path(path_str))
            except (PermissionError, OSError) as exc:
                log.warning("Could not hash %s: %s", path_str, exc)

    return folder_inventory, file_inventory


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
