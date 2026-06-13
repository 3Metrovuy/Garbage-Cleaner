import sys
from pathlib import Path

# Folder names that are always safe to auto-delete because they are fully
# regenerable from source (e.g. `npm install`, `python -m venv`).
REGENERABLE_NAMES = {
    "node_modules", "__pycache__", ".cache",
    "venv", ".venv",
    "build", "dist", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".egg-info",
}

SYSTEM_DIRS = {
    "windows", "program files", "program files (x86)",
    "programdata", "system32", "syswow64",
}


# ── Safety guard ─────────────────────────────────────────────────────────────

def assert_safe_target(target: Path) -> Path:
    """
    Resolve target and abort if it is or lives inside a system directory.
    Call this in main.py before scan().
    """
    resolved = target.resolve()
    parts = {p.lower() for p in resolved.parts}
    if parts & SYSTEM_DIRS:
        sys.exit(
            f"ERROR: Refusing to operate on system path: {resolved}\n"
            "Point the tool at a user directory (e.g. Downloads, Documents)."
        )
    return resolved


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_in_system_dir(path: Path) -> bool:
    return bool({p.lower() for p in path.parts} & SYSTEM_DIRS)


# ── Folder rules ──────────────────────────────────────────────────────────────

def classify_folders(
    folder_inventory: dict[str, dict],
) -> dict[str, str]:
    """
    Apply deterministic rules to every folder in folder_inventory.
    Returns path_str -> verdict: 'garbage' | 'keep' | 'skip' | 'unknown'.

    Processing is bottom-up: folders are sorted by depth descending so that
    when we evaluate a parent, all its children already have verdicts.
    This lets the 'all-children-are-garbage' rule work in a single pass.
    """
    verdicts: dict[str, str] = {}

    # Sort deepest-first so children are classified before parents.
    ordered = sorted(
        folder_inventory.values(),
        key=lambda f: f["depth"],
        reverse=True,
    )

    for meta in ordered:
        path = Path(meta["path"])
        name_lower = meta["name"].lower()

        # Out-of-scope system directories — skip entirely, never classify
        if _is_in_system_dir(path):
            verdicts[meta["path"]] = "skip"
            continue

        # ── Garbage rules ────────────────────────────────────────────────────

        # Empty folder: no files, no subfolders
        if meta["file_count"] == 0 and meta["subfolder_count"] == 0:
            verdicts[meta["path"]] = "garbage"
            continue

        # Recognisable regenerable folder (exact name match, case-insensitive)
        if name_lower in REGENERABLE_NAMES:
            verdicts[meta["path"]] = "garbage"
            continue

        # All direct children already classified as garbage → whole folder is garbage.
        # We check only direct children (depth == this folder's depth + 1) so we
        # don't accidentally collapse a parent because of a distant descendant.
        direct_children = [
            v for p, v in verdicts.items()
            if folder_inventory.get(p, {}).get("depth") == meta["depth"] + 1
            and Path(p).parent == path
        ]
        if direct_children and all(v == "garbage" for v in direct_children):
            verdicts[meta["path"]] = "garbage"
            continue

        # ── Keep rules ───────────────────────────────────────────────────────

        # If any direct child is kept, the folder is kept too.
        if any(v == "keep" for v in direct_children):
            verdicts[meta["path"]] = "keep"
            continue

        # Everything else goes to AI for advice
        verdicts[meta["path"]] = "unknown"

    return verdicts


# ── PDF rules ─────────────────────────────────────────────────────────────────

def classify_pdfs(pdf_inventory: dict[str, dict]) -> dict[str, str]:
    """
    Apply deterministic rules to every PDF.
    Returns path_str -> verdict: 'garbage' | 'large' | 'unknown'.

    A PDF is garbage only if it is an exact duplicate (same size + same hash);
    the oldest copy (by created_date) is kept, all others are garbage.
    Every non-duplicate, non-large PDF -> 'unknown' -> AI advice.
    PDFs are NEVER auto-classified as 'keep'.
    """
    verdicts: dict[str, str] = {}

    # Group PDFs by content hash to find duplicates.
    # Only PDFs that share a size were hashed, so content_hash may be None.
    hash_groups: dict[str, list[str]] = {}
    for path_str, meta in pdf_inventory.items():
        h = meta.get("content_hash")
        if h is not None:
            hash_groups.setdefault(h, []).append(path_str)

    # For each duplicate group, keep the oldest copy and mark the rest garbage.
    duplicate_garbage: set[str] = set()
    for paths in hash_groups.values():
        if len(paths) < 2:
            continue
        # Oldest = smallest created_date timestamp
        oldest = min(paths, key=lambda p: pdf_inventory[p]["created_date"])
        for p in paths:
            if p != oldest:
                duplicate_garbage.add(p)

    for path_str, meta in pdf_inventory.items():
        if meta["is_large"]:
            verdicts[path_str] = "large"
        elif path_str in duplicate_garbage:
            verdicts[path_str] = "garbage"
        else:
            verdicts[path_str] = "unknown"

    return verdicts
