import logging
from pathlib import Path

from rich.console import Console
from send2trash import send2trash


def run_actions(confirmed: list[dict], dry_run: bool = True) -> None:
    """
    Send all confirmed items to the OS Recycle Bin via send2trash (reversible).
    dry_run=True (default): print what would be recycled, change nothing.
    dry_run=False: move each item to the Recycle Bin and log every path.
    Folders are sent as whole units; send2trash handles both files and dirs.
    """
    log = logging.getLogger(__name__)
    console = Console()

    if not confirmed:
        console.print("[green]Nothing confirmed for deletion.[/green]")
        return

    if dry_run:
        console.print(
            f"[bold]Dry-run:[/bold] {len(confirmed)} item(s) would be sent to the Recycle Bin.\n"
        )
        for item in confirmed:
            console.print(f"  [dim]would recycle[/dim]  {item['path']}")
        return

    console.print(f"[bold]Sending {len(confirmed)} item(s) to Recycle Bin...[/bold]\n")
    recycled = 0
    for item in confirmed:
        path = Path(item["path"])
        if not path.exists():
            log.warning("SKIP (not found): %s", path)
            console.print(f"  [yellow]skip[/yellow]   {path.name}  [dim](not found)[/dim]")
            continue
        try:
            send2trash(str(path))
            log.info("RECYCLED  %s", path)
            console.print(f"  [red]recycled[/red]  {path}")
            recycled += 1
        except Exception as exc:
            log.error("FAILED to recycle %s: %s", path, exc)
            console.print(f"  [red]error[/red]   {path.name}  [dim]{exc}[/dim]")

    console.print(f"\n[bold green]{recycled}/{len(confirmed)} item(s) sent to Recycle Bin.[/bold green]")
    console.print("[dim]Undo: restore items from the Recycle Bin.[/dim]")
