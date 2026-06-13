import logging
import shutil
from pathlib import Path

from rich.console import Console


def run_actions(rows: list[dict], target: Path, apply: bool) -> None:
    """
    Dry-run (apply=False): print what would move, change nothing.
    Apply (apply=True): move all 'garbage' files into a flat _quarantine/
    folder that sits next to the scan target (not inside it).
    """
    log = logging.getLogger(__name__)
    console = Console()

    garbage = [r for r in rows if r["verdict"] == "garbage"]

    if not garbage:
        console.print("[green]No garbage files found — nothing to move.[/green]")
        return

    # Quarantine sits beside the target, not inside it, so it is never scanned
    # on subsequent runs.
    quarantine_root = target.parent / "_quarantine"

    if not apply:
        console.print(
            f"[bold]Dry-run:[/bold] {len(garbage)} file(s) would move to "
            f"[cyan]{quarantine_root}[/cyan]  (pass --apply to execute)\n"
        )
        for r in garbage:
            src = Path(r["path"])
            dest = _flat_dest(src, quarantine_root)
            console.print(f"  [dim]would move[/dim]  {src}  [dim]→[/dim]  {dest}")
        return

    # --- apply mode ---
    quarantine_root.mkdir(parents=True, exist_ok=True)
    console.print(
        f"[bold]Quarantining {len(garbage)} garbage file(s) → "
        f"[cyan]{quarantine_root}[/cyan][/bold]\n"
    )
    moved = 0
    for r in garbage:
        src = Path(r["path"])
        dest = _flat_dest(src, quarantine_root)
        try:
            shutil.move(str(src), str(dest))
            log.info("MOVED  %s  ->  %s", src, dest)
            console.print(f"  [red]moved[/red]  {src.name}  [dim]→[/dim]  {dest}")
            moved += 1
        except FileNotFoundError:
            log.warning("Source not found (already moved?): %s", src)
            console.print(f"  [yellow]skip[/yellow]   {src.name}  [dim](not found)[/dim]")
        except OSError as exc:
            log.error("Failed to move %s: %s", src, exc)
            console.print(f"  [red]error[/red]  {src.name}  [dim]{exc}[/dim]")

    console.print(
        f"\n[bold green]{moved}/{len(garbage)} file(s) quarantined.[/bold green]\n"
        f"[dim]Undo: move files from {quarantine_root} back to their original paths.\n"
        f"Original paths for every file are recorded in the run log.[/dim]"
    )


def _flat_dest(src: Path, quarantine_root: Path) -> Path:
    """
    Place src flat inside quarantine_root using only its filename.
    If a file with that name already exists, append _1, _2, ... to the stem
    until a free slot is found.
    """
    name = src.name
    dest = quarantine_root / name
    if not dest.exists():
        return dest

    stem, suffix = src.stem, src.suffix
    counter = 1
    while dest.exists():
        dest = quarantine_root / f"{stem}_{counter}{suffix}"
        counter += 1
    return dest
