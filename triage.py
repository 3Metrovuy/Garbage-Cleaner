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
MODEL = "gemini-2.5-flash"


class _FileVerdict(BaseModel):
    path: str
    verdict: Literal["garbage", "keep", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class _BatchResult(BaseModel):
    results: list[_FileVerdict]


def _build_prompt(batch: list[tuple[str, dict]]) -> str:
    now = time.time()
    lines = [
        "You are a storage-cleanup assistant. Classify each file as:",
        "  'garbage'   — junk, temporary, redundant, or clearly safe to remove",
        "  'keep'      — valuable, in active use, or important to retain",
        "  'uncertain' — not enough metadata to decide",
        "",
        "Base your decision ONLY on the metadata provided.",
        "Do NOT assume or infer file contents.",
        "Return one result per file using the exact 'path' string given.",
        "",
        "Files:",
    ]
    for path_str, meta in batch:
        mod_d = int((now - meta["modified_date"]) / 86400)
        acc_d = int((now - meta["accessed_date"]) / 86400)
        cre_d = int((now - meta["created_date"]) / 86400)
        kb = meta["size_bytes"] / 1024
        lines.append(
            f'  path="{path_str}" name="{meta["name"]}" ext="{meta["extension"]}" '
            f"size={kb:.1f}KB modified={mod_d}d_ago "
            f"accessed={acc_d}d_ago created={cre_d}d_ago"
        )
    return "\n".join(lines)


def _call_once(client: genai.Client, prompt: str) -> _BatchResult:
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_BatchResult,
        ),
    )
    return _BatchResult.model_validate_json(response.text)


def triage(unknowns: dict[str, dict]) -> dict[str, dict]:
    """
    Classify 'unknown' files via Gemini 2.5 Flash in batches of ~50.
    Returns path_str -> {verdict, confidence, reason}.
    On any batch failure: retry once, then mark all files in that batch
    as 'uncertain' and continue — never crashes the run.
    """
    log = logging.getLogger(__name__)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.error("GEMINI_API_KEY not set — all unknowns marked 'uncertain'")
        return {
            p: {
                "verdict": "uncertain",
                "confidence": 0.0,
                "reason": "GEMINI_API_KEY not set",
            }
            for p in unknowns
        }

    client = genai.Client(api_key=api_key)
    results: dict[str, dict] = {}
    items = list(unknowns.items())
    total_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE

    for b_idx, start in enumerate(range(0, len(items), BATCH_SIZE), 1):
        batch = items[start : start + BATCH_SIZE]
        batch_paths = [p for p, _ in batch]
        log.info("Triage batch %d/%d — %d files", b_idx, total_batches, len(batch))

        batch_result = None
        for attempt in (1, 2):
            try:
                batch_result = _call_once(client, _build_prompt(batch))
                break
            except Exception as exc:
                if attempt == 1:
                    log.warning("Batch %d failed (%s) — retrying once", b_idx, exc)
                    time.sleep(2)
                else:
                    log.error(
                        "Batch %d failed on retry (%s) — marking uncertain",
                        b_idx,
                        exc,
                    )

        if batch_result is None:
            for p in batch_paths:
                results[p] = {
                    "verdict": "uncertain",
                    "confidence": 0.0,
                    "reason": "API error after retry",
                }
            continue

        returned = {v.path: v for v in batch_result.results}
        for p in batch_paths:
            if p in returned:
                v = returned[p]
                results[p] = {
                    "verdict": v.verdict,
                    "confidence": v.confidence,
                    "reason": v.reason,
                }
            else:
                log.warning("Model omitted %s — marking uncertain", p)
                results[p] = {
                    "verdict": "uncertain",
                    "confidence": 0.0,
                    "reason": "Model did not return a verdict for this file",
                }

    return results
