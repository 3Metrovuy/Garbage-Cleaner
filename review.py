from rich import box
from rich.console import Console
from rich.table import Table

VERDICT_ORDER = {"garbage": 0, "uncertain": 1, "large": 2, "keep": 3}
VERDICT_STYLE = {"garbage": "red", "uncertain": "yellow", "large": "cyan", "keep": "green"}


def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def build_report(
    scan_results: dict[str, dict],
    rule_verdicts: dict[str, str],
    ai_results: dict[str, dict],
) -> list[dict]:
    """
    Merge scan metadata, rule verdicts, and AI triage results into a flat list
    of report rows, sorted by verdict severity (garbage first, keep last).
    'skip' entries (system dirs) are excluded from the report entirely.
    """
    rows: list[dict] = []

    for path_str, meta in scan_results.items():
        rule_v = rule_verdicts.get(path_str)

        if rule_v == "skip":
            continue

        if rule_v == "large":
            rows.append({
                "path": path_str,
                "name": meta["name"],
                "size_bytes": meta["size_bytes"],
                "verdict": "large",
                "confidence": None,
                "reason": "file exceeds 1 GB threshold",
            })

        elif rule_v in ("garbage", "keep"):
            rows.append({
                "path": path_str,
                "name": meta["name"],
                "size_bytes": meta["size_bytes"],
                "verdict": rule_v,
                "confidence": 1.0,
                "reason": "deterministic rule",
            })

        else:
            # rule_v == "unknown" — use AI result, fall back to uncertain
            ai = ai_results.get(path_str, {})
            rows.append({
                "path": path_str,
                "name": meta["name"],
                "size_bytes": meta["size_bytes"],
                "verdict": ai.get("verdict", "uncertain"),
                "confidence": ai.get("confidence"),
                "reason": ai.get("reason", ""),
            })

    rows.sort(key=lambda r: VERDICT_ORDER.get(r["verdict"], 99))
    return rows


def display(rows: list[dict]) -> None:
    """Print the verdict table and a one-line summary to stdout."""
    console = Console()

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Filename", max_width=45)
    table.add_column("Size", justify="right")
    table.add_column("Verdict", justify="center")
    table.add_column("Conf", justify="right")
    table.add_column("Reason")

    for r in rows:
        conf = f"{r['confidence']:.0%}" if r["confidence"] is not None else "—"
        table.add_row(
            r["name"],
            _fmt_size(r["size_bytes"]),
            r["verdict"],
            conf,
            r["reason"] or "",
            style=VERDICT_STYLE.get(r["verdict"], ""),
        )

    console.print(table)

    # Summary counts and sizes
    garbage_bytes = sum(r["size_bytes"] for r in rows if r["verdict"] == "garbage")
    large_bytes = sum(r["size_bytes"] for r in rows if r["verdict"] == "large")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    console.print(
        f"[bold]Summary:[/bold]  "
        f"[red]{counts.get('garbage', 0)} garbage[/red] ([red]{_fmt_size(garbage_bytes)}[/red] reclaimable)  "
        f"[yellow]{counts.get('uncertain', 0)} uncertain[/yellow]  "
        f"[cyan]{counts.get('large', 0)} large[/cyan] ([cyan]{_fmt_size(large_bytes)}[/cyan])  "
        f"[green]{counts.get('keep', 0)} keep[/green]"
    )
