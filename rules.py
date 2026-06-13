import os
import sys
import time
from pathlib import Path

RECENT_DAYS = 14
OLD_INSTALLER_DAYS = 180
TEMP_EXTENSIONS = {".tmp", ".log", ".crdownload", ".part"}
# Archives (.zip/.tar/.gz) intentionally excluded — let the AI tier judge them.
KEEP_EXTENSIONS = {
    ".docx", ".xlsx", ".csv", ".pptx",
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".go", ".rs",
    ".key", ".pem", ".crt", ".env",
}
INSTALLER_EXTENSIONS = {".exe", ".msi"}
# Short backstop list — the real safety is assert_safe_target(), not this set.
SYSTEM_DIRS = {
    "windows", "program files", "program files (x86)",
    "programdata", "system32", "syswow64",
}
# Generic folder-name matches for temp detection.
TEMP_DIRS = {"temp", "tmp", "cache"}

# Resolve real temp directories from env at import time so we can do
# proper prefix matching instead of relying on shell-variable strings.
_ENV_TEMP_PATHS: set[Path] = set()
for _var in ("TEMP", "TMP"):
    _val = os.environ.get(_var)
    if _val:
        try:
            _ENV_TEMP_PATHS.add(Path(_val).resolve())
        except (OSError, ValueError):
            pass

_now = time.time


def assert_safe_target(target: Path) -> Path:
    """
    Resolve target to its real absolute path and refuse to run if it is, or
    lives inside, a known system location.  Call this before scan().
    """
    resolved = target.resolve()
    parts = {p.lower() for p in resolved.parts}
    if parts & SYSTEM_DIRS:
        sys.exit(
            f"ERROR: Refusing to operate on system path: {resolved}\n"
            "Point the tool at a user directory (e.g. Downloads, Documents)."
        )
    return resolved


def _is_in_system_dir(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    return bool(parts & SYSTEM_DIRS)


def _is_in_temp_dir(path: Path) -> bool:
    # Name-based check for generic folder names (temp, tmp, cache).
    parts = {p.lower() for p in path.parts}
    if parts & TEMP_DIRS:
        return True
    # Prefix check against real paths resolved from TEMP/TMP env vars.
    resolved = path.resolve()
    return any(resolved.is_relative_to(tp) for tp in _ENV_TEMP_PATHS)


def _age_days(ts: float) -> float:
    return (_now() - ts) / 86400


def apply_rules(file_meta: dict, all_files: dict[str, dict]) -> str | None:
    """
    Return 'garbage', 'keep', 'skip', or None (unknown → pass to AI).
    'skip' means out-of-scope (system/program directory).

    file_meta keys: name, extension, size_bytes, modified_date,
                    accessed_date, created_date, content_hash, is_large,
                    path (absolute str).
    """
    ext = file_meta["extension"]
    path = Path(file_meta["path"])
    size = file_meta["size_bytes"]
    mtime = file_meta["modified_date"]
    ctime = file_meta["created_date"]
    content_hash = file_meta["content_hash"]

    # Out-of-scope: system/program directories — skip entirely
    if _is_in_system_dir(path):
        return "skip"

    # --- PDF rule (must be checked before keep rules) ---
    if ext == ".pdf":
        if content_hash is not None:
            sibling_ctimes = [
                meta["created_date"]
                for meta in all_files.values()
                if meta.get("content_hash") == content_hash
                and meta["extension"] == ".pdf"
            ]
            if len(sibling_ctimes) > 1 and ctime != min(sibling_ctimes):
                return "garbage"
        return None  # every other PDF → unknown → AI

    # --- Keep rules ---
    # Recently modified files are kept
    if _age_days(mtime) <= RECENT_DAYS:
        return "keep"

    # Known document/source/key extensions are kept
    if ext in KEEP_EXTENSIONS:
        return "keep"

    # --- Garbage rules ---
    # Exact duplicate: same size + same hash, keep only oldest by creation date
    if content_hash is not None:
        sibling_ctimes = [
            meta["created_date"]
            for meta in all_files.values()
            if meta.get("content_hash") == content_hash
        ]
        if len(sibling_ctimes) > 1 and ctime != min(sibling_ctimes):
            return "garbage"

    # Zero-byte files
    if size == 0:
        return "garbage"

    # Temp/cache extensions in temp/cache locations
    if ext in TEMP_EXTENSIONS and _is_in_temp_dir(path):
        return "garbage"

    # Old installer files in Downloads
    if (
        ext in INSTALLER_EXTENSIONS
        and "downloads" in {p.lower() for p in path.parts}
        and _age_days(ctime) > OLD_INSTALLER_DAYS
    ):
        return "garbage"

    return None  # unknown → Stage 3


def classify_all(scan_results: dict[str, dict]) -> dict[str, str]:
    """
    Run apply_rules over every non-large file in scan_results.
    Returns a dict of path_str → verdict.
    Large files are passed through as 'large'.
    """
    verdicts: dict[str, str] = {}
    # Inject path into each entry so apply_rules can use it
    enriched = {p: {**meta, "path": p} for p, meta in scan_results.items()}

    for path_str, meta in enriched.items():
        if meta["is_large"]:
            verdicts[path_str] = "large"
            continue
        result = apply_rules(meta, enriched)
        verdicts[path_str] = result if result is not None else "unknown"

    return verdicts
