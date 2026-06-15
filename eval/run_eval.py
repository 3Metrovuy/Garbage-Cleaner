"""
Eval: precision / recall of the rule layer against a labeled fixture tree.

KEY METRIC — zero false positives on auto-deletion (Group A):
  No item labeled should_auto_delete=False must ever receive a 'garbage'
  rule verdict.  A single false positive means the tool would auto-delete
  something the user wants to keep.

Secondary metric — recall on auto-deletion:
  Fraction of items labeled should_auto_delete=True that the rules catch.
  Misses (false negatives) are acceptable: they fall back to AI + human review.

Usage:
    uv run python eval/run_eval.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rules import classify_folders, classify_pdfs  # noqa: E402
from scanner import scan  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = EVAL_DIR / "fixtures"
LABELS_FILE = EVAL_DIR / "labels.json"

_DUP_BYTES = b"%PDF-1.4 duplicate-fixture\n"


# ── Fixture creation ──────────────────────────────────────────────────────────

def _write(path: Path, content: bytes = b"fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def create_fixtures() -> None:
    """Wipe and recreate the fixture tree so every run starts from a clean state."""
    import shutil
    if FIXTURES_DIR.exists():
        shutil.rmtree(FIXTURES_DIR)
    FIXTURES_DIR.mkdir(parents=True)

    # ── Group A: rules must classify these as garbage ─────────────────────────

    (FIXTURES_DIR / "empty_folder").mkdir()

    for folder, filename in [
        ("node_modules",  "package.js"),
        ("__pycache__",   "module.cpython-311.pyc"),
        (".cache",        "http_cache.db"),
        ("venv",          "pyvenv.cfg"),
        (".venv",         "pyvenv.cfg"),
        ("build",         "output.o"),
        ("dist",          "app-1.0.tar.gz"),
        (".mypy_cache",   ".version"),
        (".pytest_cache", "README.md"),
    ]:
        _write(FIXTURES_DIR / folder / filename)

    # all_garbage_parent: only garbage subfolders, no direct files
    _write(FIXTURES_DIR / "all_garbage_parent" / "node_modules" / "lib.js")
    _write(FIXTURES_DIR / "all_garbage_parent" / "__pycache__" / "cached.pyc")

    # ── Group B seeds: rules must NOT classify these as garbage ───────────────

    _write(FIXTURES_DIR / "active_project" / "main.py")
    _write(FIXTURES_DIR / "active_project" / "requirements.txt")
    _write(FIXTURES_DIR / "active_project" / "README.md")

    _write(FIXTURES_DIR / "old_backup_2019" / "data.csv")
    _write(FIXTURES_DIR / "old_backup_2019" / "notes.txt")

    _write(FIXTURES_DIR / "my_documents" / "report.docx")
    _write(FIXTURES_DIR / "my_documents" / "budget.xlsx")

    _write(FIXTURES_DIR / "photos_vacation" / "img001.jpg")
    _write(FIXTURES_DIR / "photos_vacation" / "img002.jpg")

    _write(FIXTURES_DIR / "scripts_utils" / "backup.sh")
    _write(FIXTURES_DIR / "scripts_utils" / "deploy.py")

    _write(FIXTURES_DIR / "project_beta" / "package.json")
    _write(FIXTURES_DIR / "project_beta" / "src" / "app.js")

    _write(FIXTURES_DIR / "backup_final" / "archive.zip")
    _write(FIXTURES_DIR / "backup_final" / "log.txt")

    # ── PDFs ──────────────────────────────────────────────────────────────────

    pdfs = FIXTURES_DIR / "pdfs"
    pdfs.mkdir()

    # Unique PDFs — each has distinct content, so no duplicates
    for name, tag in [
        ("report_2024.pdf",   b"report_2024"),
        ("invoice_march.pdf", b"invoice_march"),
        ("manual_v2.pdf",     b"manual_v2"),
        ("thesis_draft.pdf",  b"thesis_draft"),
    ]:
        _write(pdfs / name, b"%PDF-1.4 " + tag + b"\n")

    # Duplicate PDF set: create original first so it gets the earliest ctime.
    _write(pdfs / "dup_original.pdf", _DUP_BYTES)
    time.sleep(0.05)   # ensure distinct file-creation timestamps
    _write(pdfs / "dup_copy_1.pdf", _DUP_BYTES)
    _write(pdfs / "dup_copy_2.pdf", _DUP_BYTES)


# ── Evaluation ────────────────────────────────────────────────────────────────

def _rel(abs_path: str) -> str:
    return Path(abs_path).relative_to(FIXTURES_DIR).as_posix()


def run_eval() -> None:
    from rich import box
    from rich.console import Console
    from rich.table import Table

    console = Console()

    console.print("\n[bold]Creating fixture tree …[/bold]")
    create_fixtures()
    console.print(f"  Written to [dim]{FIXTURES_DIR}[/dim]\n")

    labels = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    folder_labels: dict[str, dict] = labels["folders"]
    pdf_labels: dict[str, dict] = labels["pdfs"]

    console.print("[bold]Running scanner …[/bold]")
    folder_inv, pdf_inv = scan(FIXTURES_DIR)
    console.print(f"  {len(folder_inv)} folder(s), {len(pdf_inv)} PDF(s) found\n")

    console.print("[bold]Running rules layer …[/bold]\n")
    folder_verdicts = classify_folders(folder_inv)
    pdf_verdicts = classify_pdfs(pdf_inv)

    # ── Build result rows ─────────────────────────────────────────────────────

    rows: list[dict] = []

    for rel_path, lbl in folder_labels.items():
        abs_path = str((FIXTURES_DIR / Path(rel_path)).resolve())
        got = folder_verdicts.get(abs_path, "MISSING")
        expected = lbl["expected_rule_verdict"]
        rows.append({
            "kind": "folder",
            "rel": rel_path,
            "expected": expected,
            "got": got,
            "correct": got == expected,
            "should_auto_delete": lbl["should_auto_delete"],
            "fp": (not lbl["should_auto_delete"]) and got == "garbage",
        })

    for rel_path, lbl in pdf_labels.items():
        abs_path = str((FIXTURES_DIR / Path(rel_path)).resolve())
        got = pdf_verdicts.get(abs_path, "MISSING")
        expected = lbl["expected_rule_verdict"]
        rows.append({
            "kind": "pdf",
            "rel": rel_path,
            "expected": expected,
            "got": got,
            "correct": got == expected,
            "should_auto_delete": lbl["should_auto_delete"],
            "fp": (not lbl["should_auto_delete"]) and got == "garbage",
        })

    # ── Results table ─────────────────────────────────────────────────────────

    tbl = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    tbl.add_column("Kind",     width=6)
    tbl.add_column("Item (relative to fixtures/)")
    tbl.add_column("Expected", justify="center", width=10)
    tbl.add_column("Got",      justify="center", width=10)
    tbl.add_column("",         width=5)

    for r in rows:
        if r["fp"]:
            icon = "[bold red]FP[/bold red]"
            got_style = "red"
        elif not r["correct"]:
            icon = "[yellow]MISS[/yellow]"
            got_style = "yellow"
        else:
            icon = "[green]ok[/green]"
            got_style = "green"

        tbl.add_row(
            r["kind"],
            r["rel"],
            r["expected"],
            f"[{got_style}]{r['got']}[/{got_style}]",
            icon,
        )

    console.print(tbl)

    # ── Summary stats ─────────────────────────────────────────────────────────

    should_delete = [r for r in rows if r["should_auto_delete"]]
    should_keep   = [r for r in rows if not r["should_auto_delete"]]

    tp = sum(1 for r in should_delete if r["got"] == "garbage")
    fn = len(should_delete) - tp
    fp = sum(1 for r in should_keep if r["got"] == "garbage")

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 1.0

    console.print(f"[bold]Labeled items:[/bold]  {len(rows)} total  "
                  f"({len(should_delete)} should auto-delete, {len(should_keep)} should not)\n")
    console.print(
        f"  [bold]Precision[/bold]  {tp}/{tp + fp} = [cyan]{precision:.0%}[/cyan]"
        f"  (of rules' garbage calls, how many were correct)"
    )
    console.print(
        f"  [bold]Recall[/bold]     {tp}/{tp + fn} = [cyan]{recall:.0%}[/cyan]"
        f"  (of labeled garbage items, how many rules caught)"
    )

    if fp == 0:
        console.print(
            "\n[bold green]PASS[/bold green] — zero false positives. "
            "The rule layer never auto-classified a safe item as garbage."
        )
    else:
        console.print(f"\n[bold red]FAIL[/bold red] — {fp} false positive(s) detected:")
        for r in rows:
            if r["fp"]:
                console.print(f"  [red]FP[/red]  {r['kind']}  {r['rel']}")
        sys.exit(1)


if __name__ == "__main__":
    run_eval()
