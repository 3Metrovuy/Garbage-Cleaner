from rich import box
from rich.console import Console
from rich.table import Table

_REC_ORDER = {"likely_garbage": 0, "needs_review": 1, "likely_keep": 2}
_REC_STYLE = {"likely_garbage": "red", "likely_keep": "green", "needs_review": "yellow"}


def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def build_report(
    folder_inventory: dict[str, dict],
    pdf_inventory: dict[str, dict],
    folder_verdicts: dict[str, str],
    pdf_verdicts: dict[str, str],
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

    for path_str, meta in pdf_inventory.items():
        verdict = pdf_verdicts.get(path_str)
        size = meta["size_bytes"]
        if verdict == "garbage":
            group_a.append({
                "path": path_str,
                "name": meta["name"],
                "size_bytes": size,
                "type": "pdf",
                "reason": "exact duplicate",
            })
        elif verdict == "large":
            group_b.append({
                "path": path_str,
                "name": meta["name"],
                "size_bytes": size,
                "type": "pdf",
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
                "type": "pdf",
                "recommendation": ai.get("recommendation", "needs_review"),
                "confidence": ai.get("confidence"),
                "reason": ai.get("reason", ""),
            })

    group_a.sort(key=lambda r: r["size_bytes"], reverse=True)
    group_b.sort(key=lambda r: _REC_ORDER.get(r["recommendation"], 99))

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
            tbl.add_row(r["type"], r["name"], _fmt_size(r["size_bytes"]), r["reason"], style="red")
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
                r["type"], r["name"], _fmt_size(r["size_bytes"]),
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
