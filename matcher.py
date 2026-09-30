from __future__ import annotations

import json

from config import (
    MATCH_MODEL,
    MATCH_EFFORT,
    MIN_EXPERIENCES,
    MAX_EXPERIENCES,
    INITIAL_EXPERIENCES,
    experience_limits,
    MIN_PROJECTS,
    MAX_PROJECTS,
    INITIAL_PROJECTS,
    project_limits,
    CAPSTONE_PROJECT_ID,
    CAPSTONE_AWARD_BULLET_ID,
    PROJECT_PRIORITY_WEIGHT,
)
from openai_service import service, UsageRecord
from project_priority import project_priority, project_selection_score
from resume_models import ResumeData
from schemas import MATCH_SCHEMA


def compact_resume(resume: ResumeData) -> dict:
    return {
        "personal": {
            "location": resume.personal.location,
        },
        "education": [
            {
                "id": edu.id,
                "institution": edu.institution,
                "degree": edu.degree,
                "location": edu.location,
                "start": edu.start,
                "end": edu.end,
                "details": edu.details,
            }
            for edu in resume.education
        ],
        "experience": [
            {
                "id": exp.id,
                "company": exp.company,
                "title": exp.title,
                "bullets": [
                    {
                        "id": b.id,
                        "text": b.text,
                        "skills": b.skills,
                        "tags": b.tags,
                        "approved_variants": b.variants,
                    }
                    for b in exp.bullets
                ],
            }
            for exp in resume.experience
        ],
        "projects": [
            {
                "id": p.id,
                "name": p.name,
                "skills": p.skills,
                "bullets": [
                    {
                        "id": b.id,
                        "text": b.text,
                        "skills": b.skills,
                        "tags": b.tags,
                        "approved_variants": b.variants,
                    }
                    for b in p.bullets
                ],
            }
            for p in resume.projects
        ],
        "skills": [
            {"category": c.category, "items": c.items}
            for c in resume.skills
        ],
    }


def sanitize_match(match: dict, resume: ResumeData) -> tuple[dict, list[str]]:
    warnings: list[str] = []

    item_ids = resume.all_item_ids()
    bullet_ids = set(resume.all_bullets())
    skills = resume.all_skill_names()

    for key in ("selected_experience_ids", "selected_project_ids"):
        raw = match.get(key, [])
        cleaned = [x for x in raw if x in item_ids]
        if len(cleaned) != len(raw):
            warnings.append(f"Removed invalid IDs from {key}.")
        match[key] = cleaned

    for key in ("selected_bullet_ids", "rewrite_bullet_ids"):
        raw = match.get(key, [])
        cleaned = [x for x in raw if x in bullet_ids]
        if len(cleaned) != len(raw):
            warnings.append(f"Removed invalid IDs from {key}.")
        match[key] = cleaned

    raw_skills = match.get("selected_skills", [])
    cleaned_skills = [x for x in raw_skills if x in skills]
    if len(cleaned_skills) != len(raw_skills):
        warnings.append("Removed model-suggested skills not present in master resume data.")

    # Preserve the model's relevance ranking. The renderer will pack as many of
    # these as fit into the configured number of skill lines.
    match["recommended_skills"] = cleaned_skills
    match["selected_skills"] = cleaned_skills

    match["item_scores"] = [
        x for x in match.get("item_scores", []) if x.get("item_id") in item_ids
    ]
    match["bullet_scores"] = [
        x for x in match.get("bullet_scores", []) if x.get("bullet_id") in bullet_ids
    ]

    # Only rewrite bullets that were actually selected.
    selected = set(match["selected_bullet_ids"])
    match["rewrite_bullet_ids"] = [
        x for x in match["rewrite_bullet_ids"] if x in selected
    ]

    return match, warnings


def apply_initial_content_budget(resume: ResumeData, match: dict) -> dict:
    """
    Build a balanced, constraint-safe seed before rewriting.

    Hard rules:
    - 2-3 experiences total; start with 3 when available.
    - Experiences use configured per-item bullet/line limits.
    - 2-3 projects total; start with 3 when available.
    - Capstone project is always selected.
    - Capstone has at least 3 bullets, including its award.
    - Capstone award bullet is always selected and is never rewritten.
    - Projects use configured per-item bullet/line limits.

    Low-relevance truthful bullets remain available to the PDF-aware filler.
    """
    item_scores = {
        row["item_id"]: int(row["score"])
        for row in match.get("item_scores", [])
    }
    bullet_scores = {
        row["bullet_id"]: int(row["score"])
        for row in match.get("bullet_scores", [])
    }

    all_bullets = resume.all_bullets()

    def est_lines(bid: str) -> int:
        bullet = all_bullets.get(bid)
        if bullet is None:
            return 1
        return max(1, (len(bullet.text) + 104) // 105)

    item_bullets: dict[str, list[str]] = {}
    for exp in resume.experience:
        item_bullets[exp.id] = [b.id for b in exp.bullets]
    for proj in resume.projects:
        item_bullets[proj.id] = [b.id for b in proj.bullets]

    def ranked_bullets(item_id: str) -> list[str]:
        return sorted(
            item_bullets.get(item_id, []),
            key=lambda bid: bullet_scores.get(bid, 0),
            reverse=True,
        )

    def choose_minimum_package(
        item_id: str,
        *,
        min_bullets: int,
        max_bullets: int,
        min_lines: int,
        max_lines: int,
        forced_ids: list[str] | None = None,
    ) -> list[str]:
        """
        Pick the strongest bullet package satisfying BOTH bullet and line minima,
        without exceeding either maximum.
        """
        chosen: list[str] = []
        line_total = 0
        forced_ids = forced_ids or []

        for bid in forced_ids:
            if bid not in item_bullets.get(item_id, []) or bid in chosen:
                continue
            lines = est_lines(bid)
            if line_total + lines <= max_lines and len(chosen) < max_bullets:
                chosen.append(bid)
                line_total += lines

        for bid in ranked_bullets(item_id):
            if bid in chosen:
                continue
            if len(chosen) >= max_bullets:
                break

            lines = est_lines(bid)
            if line_total + lines > max_lines:
                continue

            chosen.append(bid)
            line_total += lines

            if len(chosen) >= min_bullets and line_total >= min_lines:
                break

        return chosen

    # ---------------- Experience selection ----------------
    ranked_experiences = sorted(
        [exp.id for exp in resume.experience],
        key=lambda item_id: item_scores.get(item_id, 0),
        reverse=True,
    )
    experience_target_count = min(
        MAX_EXPERIENCES,
        max(MIN_EXPERIENCES, min(INITIAL_EXPERIENCES, len(ranked_experiences))),
    )
    selected_experiences = ranked_experiences[:experience_target_count]

    experience_bullets: list[str] = []
    for item_id in selected_experiences:
        package = choose_minimum_package(
            item_id,
            **experience_limits(item_id),
        )
        experience_bullets.extend(package)

    # ---------------- Project selection ----------------
    # Keep AI relevance intact and apply the small preference exactly once.
    # Save the policy and both scores for transparent reports and match.json.
    match["project_priority_weight"] = PROJECT_PRIORITY_WEIGHT
    match["project_rankings"] = [
        {
            "item_id": proj.id,
            "relevance_score": item_scores.get(proj.id, 0),
            "default_priority": project_priority(proj.id),
            "selection_score": project_selection_score(
                proj.id, item_scores.get(proj.id, 0)
            ),
            "mandatory": proj.id == CAPSTONE_PROJECT_ID,
        }
        for proj in resume.projects
    ]
    project_scores = {
        row["item_id"]: row["selection_score"]
        for row in match["project_rankings"]
    }
    ranked_projects = sorted(
        [proj.id for proj in resume.projects if proj.id != CAPSTONE_PROJECT_ID],
        key=lambda item_id: project_scores[item_id],
        reverse=True,
    )

    project_target_count = min(
        MAX_PROJECTS,
        max(MIN_PROJECTS, min(INITIAL_PROJECTS, len(resume.projects))),
    )

    selected_projects = [CAPSTONE_PROJECT_ID]
    for project_id in ranked_projects:
        if len(selected_projects) >= project_target_count:
            break
        selected_projects.append(project_id)

    # Defensive fallback if the configured capstone ID is absent.
    existing_project_ids = {p.id for p in resume.projects}
    selected_projects = [p for p in selected_projects if p in existing_project_ids]
    for project_id in ranked_projects:
        if len(selected_projects) >= min(project_target_count, len(existing_project_ids)):
            break
        if project_id not in selected_projects:
            selected_projects.append(project_id)

    project_bullets: list[str] = []
    for item_id in selected_projects:
        forced = (
            [CAPSTONE_AWARD_BULLET_ID]
            if item_id == CAPSTONE_PROJECT_ID
            else []
        )
        package = choose_minimum_package(
            item_id,
            forced_ids=forced,
            **project_limits(item_id),
        )
        project_bullets.extend(package)

    final_bullets = experience_bullets + project_bullets
    keep = set(final_bullets)

    # Preserve master chronology in rendered output.
    selected_exp_set = set(selected_experiences)
    selected_proj_set = set(selected_projects)
    match["selected_experience_ids"] = [
        exp.id for exp in resume.experience if exp.id in selected_exp_set
    ]
    match["selected_project_ids"] = [
        proj.id for proj in resume.projects if proj.id in selected_proj_set
    ]
    match["selected_bullet_ids"] = final_bullets

    # Only rewrite seed bullets selected by the model, and never rewrite the
    # capstone award statement: its exact wording must retain the 2nd-place result.
    match["rewrite_bullet_ids"] = [
        bid
        for bid in match.get("rewrite_bullet_ids", [])
        if bid in keep and bid != CAPSTONE_AWARD_BULLET_ID
    ]

    match["initial_content_budget"] = {
        "experience_count": len(match["selected_experience_ids"]),
        "project_count": len(match["selected_project_ids"]),
        "experience_bullet_count": len(experience_bullets),
        "project_bullet_count": len(project_bullets),
        "capstone_project_id": CAPSTONE_PROJECT_ID,
        "capstone_award_bullet_id": CAPSTONE_AWARD_BULLET_ID,
    }

    return match

async def match_resume(
    resume: ResumeData,
    job_analysis: dict,
) -> tuple[dict, UsageRecord, list[str]]:
    resume_payload = compact_resume(resume)

    prompt = f"""
You are selecting content from a truthful master resume for a one-page tailored
technical resume.

JOB ANALYSIS:
{json.dumps(job_analysis, indent=2)}

MASTER RESUME DATABASE:
{json.dumps(resume_payload, indent=2)}

Tasks:
1. Score EVERY experience/project item from 0-100 for relevance.
2. Score EVERY bullet from 0-100 for relevance.
3. Select the strongest experience, projects, and bullets for this role.
   For selected_skills, return a RANKED list from most relevant to least
   relevant. Include a broad enough pool (roughly 15-30 supported skills when
   available) so the renderer can fill several compact skill lines. Never add
   skills that are absent from the master resume.
4. Mark only bullets that materially benefit from wording changes in
   rewrite_bullet_ids. If a bullet is already strong, do NOT rewrite it.
5. Preserve chronology later; selection should be based on relevance only.
   Keep item_scores and bullet_scores strictly about job relevance. A small
   user preference for projects is applied separately by the downstream
   selector; do not add any preference bonus to your relevance scores.
6. Never select a skill that is absent from the master database.
7. Gaps should identify important job requirements not supported by the resume.
   Education, graduation dates, and school locations are included above and
   must be considered before declaring eligibility information missing.
8. Do not force unsupported job keywords into the resume.

Select a broad candidate pool. Professional experience will remain the
primary section, while projects will be used for technical breadth. The final
resume must always include the V2I capstone and its 2nd-place achievement. Do not
assume only highly relevant bullets may appear: lower-scoring truthful content
can still be useful when it improves completeness or fills otherwise-unused
page space. The downstream PDF packer decides what physically fits.
"""

    result, usage = await service.call_json(
        stage="matching",
        model=MATCH_MODEL,
        effort=MATCH_EFFORT,
        prompt=prompt,
        schema_name="resume_match",
        schema=MATCH_SCHEMA,
        max_output_tokens=12000,
    )

    result, warnings = sanitize_match(result, resume)
    return result, usage, warnings
