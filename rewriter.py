from __future__ import annotations

import json

from config import (
    REWRITE_MODEL,
    REWRITE_EFFORT,
    ONE_LINE_MAX,
    TWO_LINE_MIN,
    TWO_LINE_MAX,
    CAPSTONE_AWARD_BULLET_ID,
)
from openai_service import service, UsageRecord
from resume_models import ResumeData
from schemas import REWRITE_SCHEMA, LENGTH_FIX_SCHEMA


def choose_target(length: int) -> str:
    # Pick the nearest useful target band.
    if length > TWO_LINE_MAX:
        return f"{TWO_LINE_MIN}-{TWO_LINE_MAX} characters"
    distance_one = abs(length - 102)
    distance_two = abs(length - 200)
    if distance_one <= distance_two:
        return f"95-{ONE_LINE_MAX} characters"
    return f"{TWO_LINE_MIN}-{TWO_LINE_MAX} characters"


def char_status(text: str) -> str:
    n = len(text)
    if n <= ONE_LINE_MAX:
        return "good_one_line"
    if TWO_LINE_MIN <= n <= TWO_LINE_MAX:
        return "good_two_line"
    if n > TWO_LINE_MAX:
        return "too_long"
    return "inefficient_middle"


async def rewrite_selected(
    resume: ResumeData,
    job_analysis: dict,
    match: dict,
) -> tuple[dict[str, str], UsageRecord | None]:
    all_bullets = resume.all_bullets()
    selected_ids = match.get("selected_bullet_ids", [])
    rewrite_ids = set(match.get("rewrite_bullet_ids", []))

    final = {bid: all_bullets[bid].text for bid in selected_ids if bid in all_bullets}

    if not rewrite_ids:
        return final, None

    payload = []
    for bid in selected_ids:
        if bid not in rewrite_ids or bid not in all_bullets:
            continue
        bullet = all_bullets[bid]
        payload.append(
            {
                "source_id": bid,
                "source_text": bullet.text,
                "skills": bullet.skills,
                "tags": bullet.tags,
                "approved_variants": bullet.variants,
            }
        )

    prompt = f"""
Rewrite ONLY the listed resume bullets to better match the job analysis.

JOB ANALYSIS:
{json.dumps(job_analysis, indent=2)}

BULLETS:
{json.dumps(payload, indent=2)}

Rules:
- Every factual claim in the rewrite MUST be supported by its source bullet or
  an explicitly approved variant for that same bullet.
- Do not add tools, metrics, scale, responsibilities, outcomes, or technologies.
- Strong action verb, concise technical language, ATS-friendly.
- Preserve the source's substantive detail and accomplishments. A descriptive
  two-line bullet is welcome; do not reduce it to a one-line summary merely
  for brevity. Never pad a bullet or invent detail to reach a length target.
- Use job keywords only when the source fact genuinely supports them.
- Prefer either <= {ONE_LINE_MAX} characters OR {TWO_LINE_MIN}-{TWO_LINE_MAX}
  characters of visible text.
- Do not return bullets that were not supplied.
"""

    result, usage = await service.call_json(
        stage="rewrite",
        model=REWRITE_MODEL,
        effort=REWRITE_EFFORT,
        prompt=prompt,
        schema_name="bullet_rewrites",
        schema=REWRITE_SCHEMA,
    )

    for item in result.get("rewritten_bullets", []):
        sid = item.get("source_id")
        if sid in rewrite_ids and sid in final:
            final[sid] = item.get("text", final[sid]).strip()

    return final, usage


async def fix_lengths(
    resume: ResumeData,
    job_analysis: dict,
    bullets: dict[str, str],
    only_ids: set[str] | None = None,
) -> tuple[dict[str, str], list[UsageRecord], list[str]]:
    result = dict(bullets)
    usages: list[UsageRecord] = []
    warnings: list[str] = []
    all_bullets = resume.all_bullets()

    for _round in range(3):
        bad = []
        for sid, text in result.items():
            if sid == CAPSTONE_AWARD_BULLET_ID:
                continue
            if only_ids is not None and sid not in only_ids:
                continue
            status = char_status(text)
            if status in {"inefficient_middle", "too_long"}:
                source = all_bullets[sid]
                bad.append(
                    {
                        "source_id": sid,
                        "source_text": source.text,
                        "current_text": text,
                        "current_chars": len(text),
                        "target": choose_target(len(text)),
                    }
                )

        if not bad:
            break

        prompt = f"""
Fix the character lengths of these resume bullets.

JOB ANALYSIS:
{json.dumps(job_analysis, indent=2)}

BULLETS TO FIX:
{json.dumps(bad, indent=2)}

Rules:
- Hit the requested target character band for EACH bullet.
- Character count refers to visible plain text, including spaces.
- Preserve all factual meaning.
- Never add unsupported facts, tools, metrics, or outcomes.
- Keep ATS-relevant wording where supported.
- Return one fixed bullet for every supplied source_id.
"""

        fixed, usage = await service.call_json(
            stage="length_fix",
            model=REWRITE_MODEL,
            effort=REWRITE_EFFORT,
            prompt=prompt,
            schema_name="length_fixes",
            schema=LENGTH_FIX_SCHEMA,
        )
        usages.append(usage)

        for item in fixed.get("fixed_bullets", []):
            sid = item.get("source_id")
            if sid in result:
                result[sid] = item.get("text", result[sid]).strip()

    for sid, text in result.items():
        if only_ids is not None and sid not in only_ids:
            continue
        if char_status(text) in {"inefficient_middle", "too_long"}:
            warnings.append(
                f"{sid} remains {len(text)} chars ({char_status(text)})."
            )

    return result, usages, warnings


async def compress_for_page(
    resume: ResumeData,
    job_analysis: dict,
    bullets: dict[str, str],
    ids: list[str],
) -> tuple[dict[str, str], UsageRecord | None]:
    ids = [x for x in ids if x in bullets]
    if not ids:
        return bullets, None

    all_bullets = resume.all_bullets()
    payload = [
        {
            "source_id": sid,
            "source_text": all_bullets[sid].text,
            "current_text": bullets[sid],
            "target": f"90-{ONE_LINE_MAX} characters",
        }
        for sid in ids
    ]

    prompt = f"""
Compress these bullets to fit a one-page resume.

JOB ANALYSIS:
{json.dumps(job_analysis, indent=2)}

BULLETS:
{json.dumps(payload, indent=2)}

Rules:
- Each result must be 90-{ONE_LINE_MAX} characters if truthfully possible.
- Preserve the highest-value technical detail.
- Do not add any fact absent from source_text.
- Do not invent metrics, technologies, scale, or impact.
"""

    fixed, usage = await service.call_json(
        stage="page_compress",
        model=REWRITE_MODEL,
        effort=REWRITE_EFFORT,
        prompt=prompt,
        schema_name="page_compression",
        schema=LENGTH_FIX_SCHEMA,
    )

    out = dict(bullets)
    for item in fixed.get("fixed_bullets", []):
        sid = item.get("source_id")
        if sid in out:
            out[sid] = item.get("text", out[sid]).strip()
    return out, usage
