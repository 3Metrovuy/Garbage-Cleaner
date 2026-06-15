import logging
import os
import time
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()

BATCH_SIZE = 50
MODEL = "gemini-2.0-flash-lite"


# ── Pydantic schema for one AI response row ───────────────────────────────────

class _ItemAdvice(BaseModel):
    path: str
    recommendation: Literal["likely_garbage", "likely_keep"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class _BatchAdvice(BaseModel):
    results: list[_ItemAdvice]


# ── Prompt builder ────────────────────────────────────────────────────────────

def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def _build_prompt(batch: list[tuple[str, dict, str]]) -> str:
    """
    batch items are (path_str, metadata_dict, kind) where kind is 'folder' or 'pdf'.
    Folders are described name-first because the name is the highest-value signal.
    """
    now = time.time()
    lines = [
        "You are a storage-cleanup advisor helping a human decide what to delete.",
        "You NEVER make deletion decisions — you only provide a recommendation and",
        "reasoning that the human will review before anything is removed.",
        "",
        "For each item recommend exactly one of:",
        '  "likely_garbage" — probably safe to delete',
        '                     (old backup, regenerable output, redundant copy, etc.)',
        '  "likely_keep"    — probably worth keeping',
        '                     (active project, important document, recent work, etc.)',
        "",
        "Rules:",
        "- Base recommendations ONLY on the metadata shown. Do not guess file contents.",
        "- Folder NAME is the strongest signal when it is legible (e.g. node_modules,",
        "  old_backup_2019). Use metadata to disambiguate opaque names (stuff, new folder).",
        "- When a folder's name is opaque AND its metadata is inconclusive, state plainly",
        "  in your reason that you cannot determine the folder's purpose from the name alone,",
        "  and recommend 'likely_keep' so a human makes the call rather than guessing it away.",
        "- Return one result per item using the exact 'path' string given.",
        "",
        "Items:",
    ]

    for path_str, meta, kind in batch:
        if kind == "folder":
            mod_d = int((now - meta["last_modified"]) / 86400) if meta["last_modified"] else "?"
            types_str = ", ".join(
                f"{ext}:{n}" for ext, n in meta["dominant_file_types"].items()
            ) or "none"
            markers_str = ", ".join(meta["markers"]) or "none"
            lines.append(
                f'  [FOLDER] path="{path_str}" name="{meta["name"]}"'
                f' size={_fmt_size(meta["total_size"])}'
                f' files={meta["file_count"]} subfolders={meta["subfolder_count"]}'
                f' modified={mod_d}d_ago'
                f' types=[{types_str}]'
                f' markers=[{markers_str}]'
            )
        else:  # pdf
            mod_d = int((now - meta["modified_date"]) / 86400)
            cre_d = int((now - meta["created_date"]) / 86400)
            lines.append(
                f'  [PDF] path="{path_str}" name="{meta["name"]}"'
                f' size={_fmt_size(meta["size_bytes"])}'
                f' modified={mod_d}d_ago created={cre_d}d_ago'
            )

    return "\n".join(lines)


# ── Single API call ───────────────────────────────────────────────────────────

def _call_once(client: genai.Client, prompt: str) -> _BatchAdvice:
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_BatchAdvice,
        ),
    )
    return _BatchAdvice.model_validate_json(response.text)


# ── Public interface ──────────────────────────────────────────────────────────

def triage(
    unknown_folders: dict[str, dict],
    unknown_pdfs: dict[str, dict],
) -> dict[str, dict]:
    """
    Send unknown folders and PDFs to Gemini 2.5 Flash for advisory recommendations.
    Folders and PDFs are mixed into shared batches of up to 50 items each.

    Returns path_str -> {recommendation, confidence, reason} for every input path.
    Possible recommendation values:
      'likely_garbage' | 'likely_keep'  — from the model
      'needs_review'                    — AI unavailable after one retry

    The model is purely advisory: every result goes to human review regardless
    of recommendation. Nothing is deleted based on AI output alone.
    """
    log = logging.getLogger(__name__)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.error("GEMINI_API_KEY not set — all unknowns marked needs_review")
        return _all_needs_review(
            list(unknown_folders) + list(unknown_pdfs),
            "GEMINI_API_KEY not set",
        )

    client = genai.Client(api_key=api_key)
    results: dict[str, dict] = {}

    # Interleave folders and PDFs so neither type dominates any single batch.
    items: list[tuple[str, dict, str]] = (
        [(p, m, "folder") for p, m in unknown_folders.items()]
        + [(p, m, "pdf") for p, m in unknown_pdfs.items()]
    )
    total_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE

    for b_idx, start in enumerate(range(0, len(items), BATCH_SIZE), 1):
        batch = items[start : start + BATCH_SIZE]
        batch_paths = [p for p, _, _ in batch]
        log.info("Triage batch %d/%d — %d items", b_idx, total_batches, len(batch))

        advice = None
        for attempt in (1, 2):
            try:
                advice = _call_once(client, _build_prompt(batch))
                break
            except Exception as exc:
                if attempt == 1:
                    log.warning("Batch %d failed (%s) — retrying once", b_idx, exc)
                    time.sleep(2)
                else:
                    log.error(
                        "Batch %d failed on retry (%s) — marking needs_review",
                        b_idx, exc,
                    )

        if advice is None:
            results.update(_all_needs_review(batch_paths, "AI advice unavailable after retry"))
            continue

        returned = {row.path: row for row in advice.results}
        for path_str in batch_paths:
            if path_str in returned:
                row = returned[path_str]
                results[path_str] = {
                    "recommendation": row.recommendation,
                    "confidence": row.confidence,
                    "reason": row.reason,
                }
            else:
                log.warning("Model omitted %s — marking needs_review", path_str)
                results[path_str] = _needs_review_entry("Model did not return advice for this item")

    return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _needs_review_entry(reason: str) -> dict:
    return {"recommendation": "needs_review", "confidence": 0.0, "reason": reason}


def _all_needs_review(paths: list[str], reason: str) -> dict[str, dict]:
    return {p: _needs_review_entry(reason) for p in paths}
