Project: AI File & Folder Declutter Tool

A CLI tool that scans a directory and identifies disposable items. It evaluates
folders as the primary unit (most reclaimable space lives in folders), plus
loose PDF files, using deterministic rules first and an LLM only for genuinely
ambiguous folders. The LLM is an advisor: it writes reasoning for a human,
it never deletes anything. The human makes every non-rule decision through a
review UI. Confirmed items go to the Recycle Bin (reversible), never hard-deleted.

Principles (do not violate)


NEVER hard-delete. Send items to the OS Recycle Bin via send2trash (reversible).
Dry-run is the DEFAULT. Real deletion requires explicit human confirmation in the review UI.
The LLM NEVER deletes and NEVER auto-classifies as garbage. It only produces a
recommendation + written reasoning for a human. Everything the AI touches goes to human review.
The ONLY items deleted automatically are deterministic-rule hits (e.g. empty folders,
recognizable regenerable folders). These are trusted; the AI's are not.
Rules are decisive single conditions, NOT additive scores. Match → classify; no match → unknown.
The model must return structured output validated by pydantic. Never trust raw JSON.
Every run is logged to a timestamped log file (see Logging).


Stack

Python 3.11+ managed via uv.
Third-party deps (via uv add): rich, pydantic, google-genai, python-dotenv, send2trash, flask, questionary.
Standard library (built-in, no install): pathlib, hashlib, logging, os.

Setup & Run (uv)

Install deps:
uv add rich pydantic google-genai python-dotenv send2trash flask questionary

Run (opens Flask review UI by default; nothing is deleted without confirmation):
uv run python main.py <target_directory>

Terminal-only fallback (no web UI):
uv run python main.py <target_directory> --no-ui

Run the eval:
uv run python eval/run_eval.py

Stage 1 — Scanner (scanner.py)

Walk the target directory with pathlib.
Skip symlinks. Skip files/folders that raise permission errors (log and continue).
Don't follow links out of the target directory.

Collect TWO inventories:


Folders — for each folder, a descriptor keyed by folder path:
name, full path, depth, total size (sum of contained files), file count,
subfolder count, last-modified date, dominant file types inside (extensions
seen and rough counts), notable markers (presence of .git, package.json,
node_modules, venv, etc.).
Loose PDF files — for each .pdf, a descriptor keyed by file path:
name, size_bytes, modified_date, created_date. PDFs larger than 1 GB are
flagged "large" and are NOT hashed.
Do NOT hash every PDF. After the walk, among PDFs <= 1 GB, group by identical
size and compute content-hash (hashlib) ONLY for PDFs sharing a size — these
are the only possible duplicates.


Non-PDF loose files are NOT evaluated individually; they only matter as part of
their containing folder's roll-up.

Stage 2 — Rules layer (rules.py)

Apply decisive deterministic rules. Each rule either classifies confidently or
stays silent. No scoring, no thresholds. Process bottom-up (deepest folders first)
so a parent's verdict can use its children's.

Folder garbage rules (auto-delete, high confidence only):


empty folder (no files, no subfolders)
recognizable regenerable folders by exact name: node_modules, pycache,
.cache, venv, .venv, build/dist output dirs
a folder whose every contained item is already classified garbage (and no
kept subfolders) → the whole folder is garbage; delete as a unit


Folder keep / out-of-scope rules:


anything in a system or program directory (out of scope — skip entirely)
a folder containing ANY kept item is kept (one important file protects the folder)


Folders not caught by a rule → "unknown", passed to Stage 3 (AI advice).

PDF rule:


A .pdf is NEVER auto-classified as "keep".
A .pdf is "garbage" (auto, rule-based) ONLY if it is an exact duplicate
(same size AND same hash); keep the oldest copy, the rest are garbage.
Every other .pdf → "unknown", passed to Stage 3 (AI advice).


Stage 3 — AI advice (triage.py)

Send ONLY the "unknown" folders and "unknown" PDFs (never rule-decided items,
never "large" items). Batch ~50 descriptors per Gemini API call (Gemini 2.5 Flash).

For folders, the descriptor leads with the NAME (the highest-value signal when
legible — node_modules, venv, etc.), and ALWAYS includes metadata to disambiguate
when the name is opaque (stuff, new folder (2), backup_final): size, last-modified,
file count, dominant file types, and markers (.git/package.json/etc.). Name first
when legible; metadata to disambiguate when it isn't.

Per item the model returns, validated by a pydantic model:
{ recommendation: "likely_garbage" | "likely_keep", confidence: float, reason: str }
NOTE: there is no "delete" verdict — the model only advises. Every AI-evaluated
item goes to human review regardless of recommendation.

Wrap each batch call in try/except: on failure, retry once, then mark that batch's
items as "needs_review" with a note that AI advice was unavailable, and continue.
Never let one failed batch crash the run. Store results keyed by path.

Stage 4 — Review (review.py)

Produce the review data: two groups.

Group A — Confirmed garbage (rule-based, will be deleted on confirm):
auto-deleted folders and duplicate PDFs.

Group B — Needs human decision (AI-advised + AI-unavailable items):
each row shows name, size, AI recommendation, confidence, and the AI's reasoning.

Render both as rich tables for reading (always printed). Show a summary line:
total reclaimable space from Group A, and potential space from Group B.

Stage 5 — Action UI (ui_web.py + actions.py)

Default: Flask local web UI (served on localhost) is the primary review interface.


Renders Group A and Group B as HTML tables (same data as the rich tables).
Group B rows each have: a select/checkbox to mark for deletion, and a
"See directory" button.
"See directory" hits a Flask endpoint that runs os.startfile(path) SERVER-SIDE,
opening the folder in the OS file manager. This works only because the server
is the user's own local machine.
A "Confirm deletion" button sends all of Group A plus the user-selected Group B
items to deletion.


Fallback (--no-ui): a terminal review using questionary — show the rich tables,
then a checkbox prompt over Group B for the user to arrow/space-select items.

Deletion (actions.py): all confirmed items are sent to the OS Recycle Bin via
send2trash (reversible). Folders are sent as a whole unit. NEVER hard-delete.
Nothing is deleted until the human confirms in the UI. Log every deleted path.

Logging

Every run writes a timestamped log file to logs/run_YYYYMMDD_HHMMSS.log.
Record: target directory, rule-based (Group A) items, AI recommendations and
which items went to human review (Group B), skipped items (symlinks, permission
errors), every batch API failure/retry, and the exact path of every item the
human confirmed for deletion. Use Python's logging module.

Eval (eval/)

A fixture tree of ~30 labeled folders and PDFs (known correct disposition each).
A script that runs the pipeline against it and reports precision on the
rule-based auto-delete decisions (Group A) — the key metric is: the deterministic
rules NEVER auto-delete something that should be kept (zero false positives on
auto-deletion). AI recommendations can also be scored against the labels, but
they are advisory and not held to the zero-false-positive bar since a human
gates them. Report these numbers in the README.