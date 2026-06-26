from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

_REC_STYLE = {"likely_garbage": "red", "likely_keep": "green", "needs_review": "yellow"}


def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _name_cell(r: dict) -> str:
    """Indent by nesting depth and mark folders with a trailing '/'."""
    indent = "  " * r.get("indent", 0)
    name = r["name"] + "/" if r["type"] == "folder" else r["name"]
    return f"{indent}{name}"


def _hierarchical_order(items: list[dict]) -> list[dict]:
    """
    Order items so every subfolder/file sits directly beneath the parent it
    lives in (when that parent is also present in this group), and siblings are
    sorted largest-first. Each item gets an "indent" field = its nesting depth
    relative to the shallowest ancestor present in this group, for display.
    """
    by_path = {it["path"]: it for it in items}

    # For each item, find its nearest ancestor that is also in this group.
    children: dict[str | None, list[dict]] = {}
    for it in items:
        parent_key: str | None = None
        for ancestor in Path(it["path"]).parents:
            anc = str(ancestor)
            if anc in by_path:
                parent_key = anc
                break
        children.setdefault(parent_key, []).append(it)

    ordered: list[dict] = []

    def emit(parent_key: str | None, indent: int) -> None:
        kids = sorted(children.get(parent_key, []), key=lambda r: r["size_bytes"], reverse=True)
        for kid in kids:
            kid["indent"] = indent
            ordered.append(kid)
            emit(kid["path"], indent + 1)

    emit(None, 0)
    return ordered


def _covered_by_folder(file_path: str, folder_verdicts: dict[str, str]) -> bool:
    """
    True if a loose file lives inside a folder that is already accounted for —
    either auto-garbage (deleted as a unit) or an 'unknown' folder already
    offered for review on its own. Such files must NOT be listed individually:
    the folder is the review unit. Files whose only ancestors are 'skip'
    (the scan target itself, system dirs) ARE genuinely loose and listed.
    """
    for ancestor in Path(file_path).parents:
        if folder_verdicts.get(str(ancestor)) in ("garbage", "unknown"):
            return True
    return False


def build_report(
    folder_inventory: dict[str, dict],
    file_inventory: dict[str, dict],
    folder_verdicts: dict[str, str],
    file_verdicts: dict[str, str],
    ai_results: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    """
    Merge inventories, rule verdicts, and AI results into two groups.

    Group A — rule-confirmed garbage (auto-deleted on confirm).
    Group B — AI-advised or AI-unavailable items requiring human decision.

    Returns (group_a, group_b).
    """
    group_a: list[dict] = []
    group_b: list[dict] = []

    for path_str, meta in folder_inventory.items():
        verdict = folder_verdicts.get(path_str)
        if verdict in (None, "skip", "keep"):
            continue
        size = meta["total_size"]
        if verdict == "garbage":
            group_a.append({
                "path": path_str,
                "name": meta["name"],
                "size_bytes": size,
                "type": "folder",
                "reason": "deterministic rule",
            })
        else:  # unknown
            ai = ai_results.get(path_str, {})
            group_b.append({
                "path": path_str,
                "name": meta["name"],
                "size_bytes": size,
                "type": "folder",
                "recommendation": ai.get("recommendation", "needs_review"),
                "confidence": ai.get("confidence"),
                "reason": ai.get("reason", ""),
            })

    for path_str, meta in file_inventory.items():
        verdict = file_verdicts.get(path_str)
        # Duplicate PDFs are deleted as files even if they live inside a folder;
        # other loose files are only listed when not covered by a parent folder.
        if verdict != "garbage" and _covered_by_folder(path_str, folder_verdicts):
            continue
        size = meta["size_bytes"]
        ftype = "pdf" if meta.get("ext") == ".pdf" else "file"
        if verdict == "garbage":
            group_a.append({
                "path": path_str,
                "name": meta["name"],
                "size_bytes": size,
                "type": ftype,
                "reason": "exact duplicate",
            })
        elif verdict == "large":
            group_b.append({
                "path": path_str,
                "name": meta["name"],
                "size_bytes": size,
                "type": ftype,
                "recommendation": "needs_review",
                "confidence": None,
                "reason": "file exceeds 1 GB — not evaluated",
            })
        elif verdict == "unknown":
            ai = ai_results.get(path_str, {})
            group_b.append({
                "path": path_str,
                "name": meta["name"],
                "size_bytes": size,
                "type": ftype,
                "recommendation": ai.get("recommendation", "needs_review"),
                "confidence": ai.get("confidence"),
                "reason": ai.get("reason", ""),
            })

    # Sort largest-first, but keep subfolders/files directly under the parent
    # they live in so the tree reads naturally.
    group_a = _hierarchical_order(group_a)
    group_b = _hierarchical_order(group_b)

    return group_a, group_b


def display(group_a: list[dict], group_b: list[dict]) -> None:
    """Print Group A and Group B as rich tables plus a one-line summary."""
    console = Console()

    console.print("\n[bold red]Group A — Confirmed Garbage (rule-based)[/bold red]")
    if group_a:
        tbl = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
        tbl.add_column("Type", width=6)
        tbl.add_column("Name", max_width=45)
        tbl.add_column("Size", justify="right")
        tbl.add_column("Rule")
        for r in group_a:
            tbl.add_row(r["type"], _name_cell(r), _fmt_size(r["size_bytes"]), r["reason"], style="red")
        console.print(tbl)
    else:
        console.print("  [dim]None[/dim]\n")

    console.print("[bold yellow]Group B — Needs Human Decision (AI-advised)[/bold yellow]")
    if group_b:
        tbl = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
        tbl.add_column("Type", width=6)
        tbl.add_column("Name", max_width=40)
        tbl.add_column("Size", justify="right")
        tbl.add_column("AI Recommendation", justify="center")
        tbl.add_column("Conf", justify="right")
        tbl.add_column("Reason")
        for r in group_b:
            rec = r["recommendation"]
            conf = f"{r['confidence']:.0%}" if r["confidence"] is not None else "—"
            tbl.add_row(
                r["type"], _name_cell(r), _fmt_size(r["size_bytes"]),
                rec, conf, r["reason"] or "",
                style=_REC_STYLE.get(rec, ""),
            )
        console.print(tbl)
    else:
        console.print("  [dim]None[/dim]\n")

    a_bytes = sum(r["size_bytes"] for r in group_a)
    b_bytes = sum(r["size_bytes"] for r in group_b)
    console.print(
        f"[bold]Summary:[/bold]  "
        f"[red]Group A: {len(group_a)} items — {_fmt_size(a_bytes)} reclaimable[/red]  |  "
        f"[yellow]Group B: {len(group_b)} items — {_fmt_size(b_bytes)} potential[/yellow]"
    )
