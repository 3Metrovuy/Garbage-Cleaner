# Project: AI File Declutter Tool

A CLI tool that scans a directory, classifies files as garbage/keep/uncertain/large
using deterministic rules first and an LLM only for ambiguous cases, then
moves approved garbage to a quarantine folder (never hard-deletes).

## Principles (do not violate)
- NEVER delete files. Move to a `_quarantine/` folder; deletion is reversible.
- Dry-run is the DEFAULT. Real moves require an explicit `--apply` flag.
- The LLM is called ONLY for files no rule could decide. Rules are cheap and trusted.
- Rules are decisive single conditions, NOT additive scores. Match → classify; no match → unknown.
- The model must return structured output validated by pydantic. Never trust raw JSON.
- Every run is logged to a timestamped log file (see Logging).

## Stack
Python 3.11+ managed via uv.
Third-party deps (via `uv add`): rich, pydantic, google-genai, python-dotenv.
Standard library (built-in, no install): pathlib, hashlib, logging.

## Setup & Run (uv)
Install deps:
    uv add rich pydantic google-genai python-dotenv

Run (dry-run is default):
    uv run python main.py scanner.py

Apply real moves:
    uv run python main.py scanner.py --apply

Run the eval:
    uv run python eval/run_eval.py

## Stage 1 — Scanner (scanner.py)
Walk a directory with pathlib.
Skip symlinks. Skip files that raise permission errors (log and continue).
Don't follow links out of the target directory.
For each file collect into a dict  keyed by file path:
name, extension, size_bytes, modified_date, accessed_date, created_date.
Files larger than 1 GB are flagged "large" here and are NOT hashed and NOT
sent to the rules or AI (they only appear in review).
Do NOT hash every file. After the walk, among files <= 1 GB, group by identical
size and compute content-hash (hashlib) ONLY for files sharing a size — these
are the only possible duplicates.

## Stage 2 — Rules layer (rules.py)
Apply decisive deterministic rules to files NOT already flagged "large".
Each rule either classifies confidently or stays silent. No scoring, no
thresholds. If no rule fires → "unknown".

Garbage rules (high confidence only):
- exact duplicate (same size AND same hash) — keep the OLDEST copy by creation
  date, the rest are garbage
- zero-byte files
- temp/cache extensions in temp/cache locations (.tmp, .log, .crdownload, .part)
- known installer files (.exe, .msi) older than ~6 months in Downloads

Keep rules (protect from AI and from deletion):
- files modified within the last 14 days — EXCEPT PDFs (see PDF rule)
- documents/source/keys (.docx, .xlsx, .py, .key, .pem, etc.) — EXCEPT PDFs
- anything in a system or program directory (out of scope — skip entirely)

PDF rule (overrides the keep rules above):
- A .pdf file is NEVER classified as "keep".
- A .pdf is "garbage" ONLY if it is an exact duplicate (same size AND same hash);
  keep the oldest copy, the rest are garbage.
- Every other .pdf → "unknown" (passed to Stage 3), regardless of age or location.

Everything else not caught by a rule → "unknown", passed to Stage 3.

## Stage 3 — AI triage (triage.py)
Send ONLY the "unknown" files (never "large", "keep", or rule-decided garbage).
Batch ~50 metadata descriptors per Gemini API call (use Gemini 2.5 Flash).
Per file the model returns, validated by a pydantic model:
{ verdict: "garbage" | "keep" | "uncertain", confidence: float, reason: str }
Wrap each batch call in try/except: on failure, retry once, then mark that
batch's files as "uncertain" and continue. Never let one failed batch crash the run.
Store results in a dict keyed by file path.

## Stage 4 — Review (review.py)
Merge rule results + AI results + large files. Print a rich table:
columns = filename, size, verdict, confidence, reason.
Sort by verdict (garbage → uncertain → large → keep). Show a summary line:
total reclaimable space from garbage, and total space held by large files.

## Stage 5 — Safe action (actions.py)
Default (no flag): dry-run — print what WOULD move, change nothing.
With --apply: move ONLY "garbage" files into _quarantine/ (preserve relative
paths so undo is possible). NEVER touch "keep", "uncertain", or "large" files.
Print an undo hint.

## Logging
Every run writes a timestamped log file to logs/run_YYYYMMDD_HHMMSS.log.
Record: target directory, flags used (dry-run vs --apply), counts per verdict,
skipped files (symlinks, permission errors), every batch API failure/retry,
and in --apply mode the exact source→quarantine path of every moved file
(this is what makes undo possible). Use Python's logging module.

## Eval (eval/)
A fixture folder of ~30 labeled files (known correct verdict each). A script
that runs the pipeline against it and reports precision on garbage calls —
the key metric is: we never flag a "keep" file as garbage (zero false positives
on deletion). Report this number in the README.