import argparse
import logging
from datetime import datetime
from pathlib import Path

from actions import run_actions
from review import build_report, display
from rules import assert_safe_target, classify_files, classify_folders
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
    """
    Single checkbox prompt over both groups. Group A (rule-confirmed) is
    pre-checked but can be unchecked to keep; Group B is unchecked by default.
    Nothing is deleted until the user confirms. Returns confirmed items.
    """
    import questionary

    choices: list = []
    if group_a:
        choices.append(questionary.Separator("── Group A — rule-confirmed garbage (pre-selected) ──"))
        choices += [
            questionary.Choice(
                title=f"[rule]  {r['name']}  ({_fmt_size(r['size_bytes'])})",
                value=r,
                checked=True,
            )
            for r in group_a
        ]
    if group_b:
        choices.append(questionary.Separator("── Group B — needs your decision ──"))
        choices += [
            questionary.Choice(
                title=f"[{r['recommendation']}]  {r['name']}  ({_fmt_size(r['size_bytes'])})",
                value=r,
                checked=False,
            )
            for r in group_b
        ]

    if not choices:
        print("Nothing to review — exiting.")
        return []

    selected = questionary.checkbox(
        "Select items to recycle (space to toggle, enter to confirm). "
        "Group A is pre-checked — uncheck anything you want to keep:",
        choices=choices,
    ).ask() or []

    if not selected:
        print("Nothing selected — exiting.")
        return []

    print(f"\n{len(selected)} item(s) selected for the Recycle Bin.")
    ok = questionary.confirm("Send all confirmed items to the Recycle Bin?").ask()
    return selected if ok else []


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
    folder_inventory, file_inventory = scan(target)
    log.info("Scan complete: %d folders, %d files", len(folder_inventory), len(file_inventory))
    print(f"Found {len(folder_inventory)} folders and {len(file_inventory)} files.")

    # Stage 2 — Rules
    folder_verdicts = classify_folders(folder_inventory)
    file_verdicts = classify_files(file_inventory)

    unknown_folders = {p: m for p, m in folder_inventory.items() if folder_verdicts.get(p) == "unknown"}
    # Only genuinely-loose files reach the AI: a file inside a garbage or
    # already-reviewable folder is covered by that folder, not listed on its own.
    unknown_files = {
        p: m for p, m in file_inventory.items()
        if file_verdicts.get(p) == "unknown"
        and not any(folder_verdicts.get(str(a)) in ("garbage", "unknown") for a in Path(p).parents)
    }
    log.info(
        "Rules: %d garbage folders, %d garbage files, %d unknown folders, %d unknown files",
        sum(1 for v in folder_verdicts.values() if v == "garbage"),
        sum(1 for v in file_verdicts.values() if v == "garbage"),
        len(unknown_folders),
        len(unknown_files),
    )

    # Stage 3 — AI triage (unknowns only)
    ai_results: dict[str, dict] = {}
    if unknown_folders or unknown_files:
        print(f"Sending {len(unknown_folders)} folders and {len(unknown_files)} files to AI ...")
        ai_results = triage(unknown_folders, unknown_files)
        log.info("AI triage complete: %d results", len(ai_results))

    # Stage 4 — Report
    group_a, group_b = build_report(
        folder_inventory, file_inventory,
        folder_verdicts, file_verdicts,
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

    # Regular web mode: nothing is recycled up front. Group A is shown
    # pre-selected and Group B unchecked; the user confirms in the UI, and all
    # deletions happen there via the "Delete selected" button.
    launch(group_a, group_b, preview=False)


if __name__ == "__main__":
    main()
