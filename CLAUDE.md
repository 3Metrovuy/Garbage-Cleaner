Project: AI File & Folder Declutter Tool

A CLI tool that scans a directory and identifies disposable items. It evaluates
folders as the primary unit (most reclaimable space lives in folders), plus
loose files (files not contained in a folder that is itself a review unit),
using deterministic rules first and an LLM only for genuinely
ambiguous folders. The LLM is an advisor: it writes reasoning for a human,
it never deletes anything. The human makes every non-rule decision through a
review UI. Confirmed items go to the Recycle Bin (reversible), never hard-deleted.

Principles (do not violate)


NEVER hard-delete. Send items to the OS Recycle Bin via send2trash (reversible).
Nothing is EVER deleted without explicit human confirmation in the review UI.
A --dry-run flag runs the full pipeline, prints the report, and exits without
opening the deletion UI and without deleting anything.
The LLM NEVER deletes and NEVER auto-classifies as garbage. It only produces a
recommendation + written reasoning for a human. Everything the AI touches goes to human review.
Nothing is deleted up front, not even rule hits. Deterministic-rule hits
(Group A: empty folders, recognizable regenerable folders, duplicate PDFs) are
trusted enough to be PRE-SELECTED (checked by default) in the review UI, but
they are still only recycled when the human clicks "Delete selected" — and the
human may uncheck any of them to keep it. Closing the UI without confirming
deletes nothing.
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

Preview only (print report, never open the deletion UI, never delete):
uv run python main.py <target_directory> --dry-run

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
Loose files — for EVERY file, a descriptor keyed by file path:
name, ext, size_bytes, modified_date, created_date. Files larger than 1 GB are
flagged "large" and are NOT hashed.
Do NOT hash every file. After the walk, among .pdf files <= 1 GB, group by
identical size and compute content-hash (hashlib) ONLY for PDFs sharing a size.
Hashing is restricted to PDFs because exact-duplicate AUTO-DELETION is a
PDF-only rule (see Stage 2); other file types are never auto-deleted, so there
is no need to hash them.

The scanner collects every file, but a file is only OFFERED for review when it
is genuinely "loose" — see Stage 4. A file inside a folder that is itself a
review unit (auto-garbage, or an "unknown" folder shown on its own) is covered
by that folder and is NOT listed individually; it still contributes to that
folder's size roll-up. In practice this surfaces files sitting directly in the
scan target (e.g. an un-organized Downloads folder) without dumping the
internals of every project folder.

Stage 2 — Rules layer (rules.py)

Apply decisive deterministic rules. Each rule either classifies confidently or
stays silent. No scoring, no thresholds. Process bottom-up (deepest folders first)
so a parent's verdict can use its children's.

Folder garbage rules (auto-delete) — a folder is auto-deleted ONLY when the
folder ITSELF matches one of these. NEVER infer auto-delete from a folder's
contents:


empty folder (no files, no subfolders)
recognizable regenerable folder by EXACT name: node_modules, __pycache__,
.cache, venv, .venv, build/dist output dirs. Deleted as a whole unit.
Rationale: these names are unambiguous and fully regenerable, and it is
acceptably unlikely that irreplaceable data is hidden inside one — so a
recognizable folder is trusted even though its contents are not inspected.


There is deliberately NO "every contained item is garbage → the whole folder
is garbage" rule. A parent is NOT auto-deleted just because its subfolders are
garbage. Such a parent almost always has an opaque, unrecognizable name (stuff,
backup_final, new folder (2), myproject) and may ALSO hold loose files that are
not listed individually once the folder is shown as its own review unit —
collapsing it to garbage as a unit would silently recycle those files. It must
fall through to "unknown" instead.


Folder out-of-scope rule:


anything in a system or program directory (out of scope — skip entirely)


Folders not caught by a garbage rule → "unknown", passed to Stage 3 (AI advice).
This EXPLICITLY includes any folder with an opaque/unrecognizable name, even
when every subfolder inside it is itself rule-garbage. We cannot judge an
opaque folder from its name, and it is far safer to ask the AI + human than to
guess. (Because the scanner no longer needs direct-vs-total file counts for any
rule, the only protection a folder's loose files have is this fall-through —
so an opaque-named folder must never be auto-deleted.)

File rule (rules.py classify_files):


A file is NEVER auto-classified as "keep".
A file is "garbage" (auto, rule-based) ONLY if it is an exact-duplicate .pdf
(same size AND same hash); keep the oldest copy, the rest are garbage. This is
the ONLY auto-delete path for a file — no non-PDF file is ever auto-deleted,
because only PDFs are hashed.
A file > 1 GB → "large" (flagged, not evaluated).
Every other file → "unknown", passed to Stage 3 (AI advice).


Stage 3 — AI advice (triage.py)

Send ONLY the "unknown" folders and "unknown" loose files (never rule-decided
items, never "large" items, never files covered by a parent review-unit folder).
Batch ~50 descriptors per Gemini API call (Gemini 2.5 Flash).

For folders, the descriptor leads with the NAME (the highest-value signal when
legible — node_modules, venv, etc.), and ALWAYS includes metadata to disambiguate
when the name is opaque (stuff, new folder (2), backup_final): size, last-modified,
file count, dominant file types, and markers (.git/package.json/etc.). Name first
when legible; metadata to disambiguate when it isn't.

When a folder's name is opaque AND its metadata is inconclusive, the model must
say plainly in its reason that it cannot determine the folder's contents or
purpose from the name alone, and lean conservative — recommend "likely_keep" so
a human makes the call rather than guessing the folder away.

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
Subfolders/loose files are ordered directly beneath the parent folder they live
in (indented, largest-first); folders are marked with a trailing "/" and a
distinct colour so the tree is legible.
Group B rows each have: a select/checkbox to mark for deletion, and an
"Open" button.
"Open" hits a Flask endpoint that runs os.startfile SERVER-SIDE, opening the
folder (for a loose file, its CONTAINING folder) in the OS file manager. This
works only because the server is the user's own local machine. The endpoint
MUST validate the requested path against the set of paths actually present in
this review (Group A + Group B) and reject anything else — never call
os.startfile on an arbitrary client-supplied path.
Both groups have row checkboxes and a per-table select-all: Group A rows are
pre-checked (rule-confirmed), Group B rows start unchecked. A single "Delete
selected" button at the top recycles ALL checked items (Group A + Group B) in
one batch — there is no per-row delete button, and nothing is recycled before
this click. Unchecking a Group A row keeps it. Closing the UI without clicking
deletes nothing.


Bind the server to an OS-assigned free port (port 0) and read back the actual
port, then open the browser to it. NEVER hardcode port 5000, and never let a
busy port crash the run.


The web UI must NEVER hang the CLI. If the user closes the browser tab or
interrupts the process (Ctrl-C / KeyboardInterrupt) without choosing, treat it
as a cancel — delete nothing, shut the server down cleanly, and return.


Fallback (--no-ui): a terminal review using questionary — show the rich tables,
then a single checkbox prompt over BOTH groups (Group A pre-checked, Group B
unchecked) for the user to arrow/space-select, followed by a confirm prompt.

Deletion (actions.py): all confirmed items are sent to the OS Recycle Bin via
send2trash (reversible). Folders are sent as a whole unit. NEVER hard-delete.
Nothing is deleted until the human confirms in the UI. Log every deleted path.
actions.py keeps a real, honored dry_run parameter (default True): under
--dry-run main.py prints the report and the would-recycle preview and returns
without opening the deletion UI; only a confirmed, non-dry-run path deletes.

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

When the rules change above is applied, the eval fixtures/labels must be updated
to match: an opaque-named parent that contains only garbage subfolders now
expects "unknown" (not "garbage"). Add a regression fixture that locks in the
bug fix — an opaque-named folder holding BOTH a garbage subfolder (e.g.
node_modules) AND a loose non-PDF file — labeled should_auto_delete=false,
expected_rule_verdict="unknown". This must never receive a "garbage" verdict,
proving loose files are no longer collateral-deleted when a sibling subfolder
is garbage.