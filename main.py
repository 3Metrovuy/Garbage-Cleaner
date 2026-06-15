import argparse
import logging
from datetime import datetime
from pathlib import Path

from actions import run_actions
from review import build_report, display
from rules import assert_safe_target, classify_folders, classify_pdfs
from scanner import scan
from triage import triage


def _setup_logging() -> Path:
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"run_{timestamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8")],
    )
    return log_file


def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _terminal_ui(group_a: list[dict], group_b: list[dict]) -> list[dict]:
    """Checkbox prompt over Group B; Group A is always included. Returns confirmed items."""
    import questionary

    selected_b: list[dict] = []
    if group_b:
        choices = [
            questionary.Choice(
                title=f"[{r['recommendation']}]  {r['name']}  ({_fmt_size(r['size_bytes'])})",
                value=r,
            )
            for r in group_b
        ]
        selected_b = questionary.checkbox(
            "Select Group B items to include in deletion (space to toggle, enter to confirm):",
            choices=choices,
        ).ask() or []

    confirmed = group_a + selected_b
    if not confirmed:
        print("Nothing selected — exiting.")
        return []

    print(f"\n{len(group_a)} Group A + {len(selected_b)} Group B item(s) selected.")
    ok = questionary.confirm("Send all confirmed items to the Recycle Bin?").ask()
    return confirmed if ok else []


def main() -> None:
    parser = argparse.ArgumentParser(description="AI File & Folder Declutter Tool")
    parser.add_argument("target", type=Path, help="Directory to scan")
    parser.add_argument("--no-ui", action="store_true", help="Terminal-only mode (no Flask web UI)")
    parser.add_argument("--dry-run", action="store_true", help="Print report and exit without opening UI or deleting")
    args = parser.parse_args()

    log_file = _setup_logging()
    log = logging.getLogger(__name__)
    print(f"Logging to {log_file}")

    # Abort if target is a system directory
    target = assert_safe_target(args.target)
    log.info("Target directory: %s", target)

    # Stage 1 — Scan
    print(f"Scanning {target} ...")
    folder_inventory, pdf_inventory = scan(target)
    log.info("Scan complete: %d folders, %d PDFs", len(folder_inventory), len(pdf_inventory))
    print(f"Found {len(folder_inventory)} folders and {len(pdf_inventory)} PDFs.")

    # Stage 2 — Rules
    folder_verdicts = classify_folders(folder_inventory)
    pdf_verdicts = classify_pdfs(pdf_inventory)

    unknown_folders = {p: m for p, m in folder_inventory.items() if folder_verdicts.get(p) == "unknown"}
    unknown_pdfs = {p: m for p, m in pdf_inventory.items() if pdf_verdicts.get(p) == "unknown"}
    log.info(
        "Rules: %d garbage folders, %d garbage PDFs, %d unknown folders, %d unknown PDFs",
        sum(1 for v in folder_verdicts.values() if v == "garbage"),
        sum(1 for v in pdf_verdicts.values() if v == "garbage"),
        len(unknown_folders),
        len(unknown_pdfs),
    )

    # Stage 3 — AI triage (unknowns only)
    ai_results: dict[str, dict] = {}
    if unknown_folders or unknown_pdfs:
        print(f"Sending {len(unknown_folders)} folders and {len(unknown_pdfs)} PDFs to AI ...")
        ai_results = triage(unknown_folders, unknown_pdfs)
        log.info("AI triage complete: %d results", len(ai_results))

    # Stage 4 — Report
    group_a, group_b = build_report(
        folder_inventory, pdf_inventory,
        folder_verdicts, pdf_verdicts,
        ai_results,
    )
    log.info("Report: Group A=%d items, Group B=%d items", len(group_a), len(group_b))
    for item in group_a:
        log.info("GROUP_A  %s", item["path"])
    for item in group_b:
        log.info("GROUP_B  %s  recommendation=%s", item["path"], item.get("recommendation"))

    display(group_a, group_b)

    # Stage 5 — UI + deletion
    if args.no_ui:
        if args.dry_run:
            print(f"\n[dry-run] Would recycle {len(group_a)} Group A item(s):")
            for item in group_a:
                print(f"  {item['path']}")
            print(f"[dry-run] {len(group_b)} Group B item(s) would go to human review.")
            print("[dry-run] Exiting — nothing deleted.")
            return
        confirmed = _terminal_ui(group_a, group_b)
        if confirmed:
            for item in confirmed:
                log.info("CONFIRMED FOR DELETION: %s", item["path"])
            run_actions(confirmed, dry_run=False)
        else:
            print("No items confirmed — nothing deleted.")
        return

    try:
        from ui_web import launch
    except ImportError:
        print("ui_web.py not found — falling back to terminal UI.")
        log.warning("ui_web not available — using terminal UI")
        confirmed = _terminal_ui(group_a, group_b)
        if confirmed:
            for item in confirmed:
                log.info("CONFIRMED FOR DELETION: %s", item["path"])
            run_actions(confirmed, dry_run=False)
        return

    if args.dry_run:
        # Preview mode: show full report in browser, delete nothing
        print("Opening dry-run preview in browser — nothing will be deleted.")
        launch(group_a, group_b, preview=True)
        return

    # Regular web mode: auto-recycle Group A now, then open UI for Group B
    if group_a:
        print(f"Recycling {len(group_a)} rule-confirmed item(s) ...")
        for item in group_a:
            log.info("AUTO-RECYCLED GROUP A: %s", item["path"])
        run_actions(group_a, dry_run=False)

    launch(group_a, group_b, preview=False)
    # Group B deletions happen per-row inside the web UI server


if __name__ == "__main__":
    main()
