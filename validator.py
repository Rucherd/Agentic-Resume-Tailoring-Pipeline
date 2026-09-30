from __future__ import annotations

import json
from difflib import SequenceMatcher

from config import VALIDATION_MODEL, VALIDATION_EFFORT
from openai_service import service, UsageRecord
from resume_models import ResumeData
from schemas import TRUTH_SCHEMA
from rewriter import char_status


async def truth_check(
    resume: ResumeData,
    bullets: dict[str, str],
    ids: set[str] | None = None,
) -> tuple[dict[str, bool], dict[str, list[str]], UsageRecord | None]:
    all_bullets = resume.all_bullets()
    payload = []

    for sid, candidate in bullets.items():
        if ids is not None and sid not in ids:
            continue
        if sid not in all_bullets:
            continue
        source = all_bullets[sid]
        payload.append(
            {
                "source_id": sid,
                "source_text": source.text,
                "approved_variants": source.variants,
                "candidate_text": candidate,
            }
        )

    if not payload:
        return {}, {}, None

    prompt = f"""
Act as a strict factual resume auditor.

For each candidate bullet, determine whether EVERY concrete claim is supported
by the source bullet or one of its approved variants.

A wording change or reasonable compression is fine.
The following are NOT fine unless explicitly supported:
- new metrics or numbers,
- new technologies,
- new business outcomes

BULLETS:
{json.dumps(payload, indent=2)}

If even one material claim is unsupported, supported must be false and
unsupported_claims must identify it.
"""

    checked, usage = await service.call_json(
        stage="truth_validation",
        model=VALIDATION_MODEL,
        effort=VALIDATION_EFFORT,
        prompt=prompt,
        schema_name="truth_checks",
        schema=TRUTH_SCHEMA,
    )

    support: dict[str, bool] = {}
    unsupported: dict[str, list[str]] = {}
    for row in checked.get("checks", []):
        sid = row.get("source_id")
        if sid in bullets:
            support[sid] = bool(row.get("supported"))
            unsupported[sid] = row.get("unsupported_claims", [])

    return support, unsupported, usage


def duplicate_pairs(bullets: dict[str, str], threshold: float = 0.88) -> list[tuple[str, str, float]]:
    ids = list(bullets)
    duplicates = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a = " ".join(bullets[ids[i]].lower().split())
            b = " ".join(bullets[ids[j]].lower().split())
            ratio = SequenceMatcher(None, a, b).ratio()
            if ratio >= threshold:
                duplicates.append((ids[i], ids[j], ratio))
    return duplicates


def keyword_coverage(priority_keywords: list[str], resume_text: str) -> dict:
    haystack = resume_text.lower()
    found = []
    missing = []
    for keyword in priority_keywords:
        if keyword.lower() in haystack:
            found.append(keyword)
        else:
            missing.append(keyword)
    total = len(priority_keywords)
    percent = round(100 * len(found) / total, 1) if total else 100.0
    return {"percent": percent, "found": found, "missing": missing}


def local_qa(bullets: dict[str, str]) -> dict:
    lengths = {
        sid: {
            "chars": len(text),
            "status": char_status(text),
        }
        for sid, text in bullets.items()
    }
    return {
        "bullet_lengths": lengths,
        "duplicate_pairs": [
            {"a": a, "b": b, "similarity": round(score, 3)}
            for a, b, score in duplicate_pairs(bullets)
        ],
    }
