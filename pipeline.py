from __future__ import annotations

import argparse
import asyncio
import copy
import json
from collections import Counter
from pathlib import Path

from compiler import cleanup_aux, compile_tex, page_count
from experience_profiles import MINIVEIL_PROFILES, prepare_job_resume
from config import (
    JOBS_DIR,
    OUTPUT_DIR,
    RESUME_DATA,
    TEMPLATE,
    MAX_PAGE_FIX_ROUNDS,
    FILL_MIN_BULLET_SCORE,
    MAX_FILL_TRIALS,
    MAX_BALANCE_TRIALS,
    MIN_EXPERIENCES,
    MAX_EXPERIENCES,
    experience_limits,
    TARGET_EXPERIENCE_BULLETS,
    MAX_EXPERIENCE_BULLETS,
    MIN_PROJECTS,
    MAX_PROJECTS,
    project_limits,
    TARGET_PROJECT_BULLETS,
    MAX_PROJECT_BULLETS,
    CAPSTONE_PROJECT_ID,
    CAPSTONE_AWARD_BULLET_ID,
)
from jd_analyzer import analyze_job
from matcher import match_resume, apply_initial_content_budget
from openai_service import UsageRecord
from project_priority import project_selection_score
from renderer import render_resume
from report import write_json, write_report, total_cost
from resume_models import ResumeData
from section_balance import (
    balance_policy, balance_summary, content_balance_distance, section_totals,
)
from rewriter import rewrite_selected, fix_lengths, compress_for_page
from validator import truth_check, local_qa, keyword_coverage


def load_resume() -> ResumeData:
    if not RESUME_DATA.exists():
        raise FileNotFoundError(
            f"{RESUME_DATA.name} not found. Run bootstrap_resume.py or copy "
            "resume_data.example.json to resume_data.json and edit it."
        )
    return ResumeData.model_validate_json(RESUME_DATA.read_text(encoding="utf-8"))


def bullet_score_map(match: dict) -> dict[str, int]:
    return {x["bullet_id"]: int(x["score"]) for x in match.get("bullet_scores", [])}


def item_for_bullet(resume: ResumeData) -> dict[str, str]:
    mapping = {}
    for exp in resume.experience:
        for bullet in exp.bullets:
            mapping[bullet.id] = exp.id
    for project in resume.projects:
        for bullet in project.bullets:
            mapping[bullet.id] = project.id
    return mapping


def selected_counts(resume: ResumeData, match: dict) -> Counter:
    mapping = item_for_bullet(resume)
    counts = Counter()
    for bid in match.get("selected_bullet_ids", []):
        if bid in mapping:
            counts[mapping[bid]] += 1
    return counts


def pick_page_compression_ids(resume: ResumeData, match: dict, bullets: dict[str, str]) -> list[str]:
    scores = bullet_score_map(match)
    mapping = item_for_bullet(resume)
    project_ids = {project.id for project in resume.projects}

    candidates = []
    for bid, text in bullets.items():
        if bid == CAPSTONE_AWARD_BULLET_ID:
            continue
        item_id = mapping.get(bid)
        if not item_id:
            continue
        # Prefer compressing lower-value bullets that currently look like two lines.
        if len(text) > 105:
            score = scores.get(bid, 50)
            if item_id in project_ids:
                score = project_selection_score(item_id, score)
            trial = {**bullets, bid: text[:105]}
            distance = content_balance_distance(resume, match, trial)
            candidates.append((distance, score, -len(text), bid))

    candidates.sort()
    return [x[3] for x in candidates[:2]]


def item_score_map(match: dict) -> dict[str, int]:
    return {x["item_id"]: int(x["score"]) for x in match.get("item_scores", [])}


def item_type_map(resume: ResumeData) -> dict[str, str]:
    result = {exp.id: "experience" for exp in resume.experience}
    result.update({proj.id: "project" for proj in resume.projects})
    return result


def sync_selected_items(resume: ResumeData, match: dict, bullets: dict[str, str]) -> None:
    mapping = item_for_bullet(resume)
    active_items = {mapping[bid] for bid in bullets if bid in mapping}
    match["selected_experience_ids"] = [
        exp.id for exp in resume.experience if exp.id in active_items
    ]
    match["selected_project_ids"] = [
        proj.id for proj in resume.projects if proj.id in active_items
    ]
    match["selected_bullet_ids"] = [
        bid for bid in match.get("selected_bullet_ids", []) if bid in bullets
    ]


def current_section_counts(
    resume: ResumeData,
    bullets: dict[str, str],
) -> tuple[Counter, Counter]:
    mapping = item_for_bullet(resume)
    types = item_type_map(resume)
    exp_counts = Counter()
    proj_counts = Counter()

    for bid in bullets:
        item_id = mapping.get(bid)
        if not item_id:
            continue
        if types.get(item_id) == "experience":
            exp_counts[item_id] += 1
        else:
            proj_counts[item_id] += 1

    return exp_counts, proj_counts


def estimated_lines(text: str) -> int:
    return max(1, (len(text) + 104) // 105)


def section_line_counts(
    resume: ResumeData,
    bullets: dict[str, str],
) -> tuple[Counter, Counter]:
    mapping = item_for_bullet(resume)
    types = item_type_map(resume)
    exp_lines = Counter()
    proj_lines = Counter()

    for bid, text in bullets.items():
        item_id = mapping.get(bid)
        if not item_id:
            continue
        if types.get(item_id) == "experience":
            exp_lines[item_id] += estimated_lines(text)
        else:
            proj_lines[item_id] += estimated_lines(text)

    return exp_lines, proj_lines


def bullets_for_item(resume: ResumeData, item_id: str) -> list[str]:
    for exp in resume.experience:
        if exp.id == item_id:
            return [b.id for b in exp.bullets]
    for project in resume.projects:
        if project.id == item_id:
            return [b.id for b in project.bullets]
    return []


def layout_constraint_violations(
    resume: ResumeData,
    match: dict,
    bullets: dict[str, str],
) -> list[str]:
    """
    Validate every hard layout rule against the CURRENT bullet wording.
    """
    violations: list[str] = []
    exp_counts, proj_counts = current_section_counts(resume, bullets)
    exp_lines, proj_lines = section_line_counts(resume, bullets)

    active_exp = [item_id for item_id, n in exp_counts.items() if n > 0]
    active_proj = [item_id for item_id, n in proj_counts.items() if n > 0]

    if not (MIN_EXPERIENCES <= len(active_exp) <= MAX_EXPERIENCES):
        violations.append(
            f"experience count {len(active_exp)} outside "
            f"{MIN_EXPERIENCES}-{MAX_EXPERIENCES}"
        )

    if not (MIN_PROJECTS <= len(active_proj) <= MAX_PROJECTS):
        violations.append(
            f"project count {len(active_proj)} outside "
            f"{MIN_PROJECTS}-{MAX_PROJECTS}"
        )

    for item_id in active_exp:
        count = exp_counts[item_id]
        lines = exp_lines[item_id]
        limits = experience_limits(item_id)
        if not (limits["min_bullets"] <= count <= limits["max_bullets"]):
            violations.append(
                f"{item_id}: {count} experience bullets outside "
                f"{limits['min_bullets']}-{limits['max_bullets']}"
            )
        if not (limits["min_lines"] <= lines <= limits["max_lines"]):
            violations.append(
                f"{item_id}: ~{lines} experience lines outside "
                f"{limits['min_lines']}-{limits['max_lines']}"
            )

    for item_id in active_proj:
        count = proj_counts[item_id]
        lines = proj_lines[item_id]
        limits = project_limits(item_id)
        if not (limits["min_bullets"] <= count <= limits["max_bullets"]):
            violations.append(
                f"{item_id}: {count} project bullets outside "
                f"{limits['min_bullets']}-{limits['max_bullets']}"
            )
        if not (limits["min_lines"] <= lines <= limits["max_lines"]):
            violations.append(
                f"{item_id}: ~{lines} project lines outside "
                f"{limits['min_lines']}-{limits['max_lines']}"
            )

    if CAPSTONE_PROJECT_ID not in active_proj:
        violations.append("mandatory capstone project is missing")

    if CAPSTONE_AWARD_BULLET_ID not in bullets:
        violations.append("mandatory capstone 2nd-place bullet is missing")
    else:
        award_text = bullets[CAPSTONE_AWARD_BULLET_ID].lower()
        if "2nd" not in award_text and "second" not in award_text:
            violations.append("capstone award bullet no longer mentions 2nd place")

    if sum(exp_counts.values()) > MAX_EXPERIENCE_BULLETS:
        violations.append(
            f"experience section has {sum(exp_counts.values())} bullets; "
            f"maximum is {MAX_EXPERIENCE_BULLETS}"
        )

    if sum(proj_counts.values()) > MAX_PROJECT_BULLETS:
        violations.append(f"project section exceeds {MAX_PROJECT_BULLETS} bullets")

    return violations


def ensure_mandatory_capstone(
    resume: ResumeData,
    match: dict,
    bullets: dict[str, str],
    available_texts: dict[str, str],
) -> None:
    """Force the capstone and exact 2nd-place bullet into the selected resume."""
    project_ids = {p.id for p in resume.projects}
    if CAPSTONE_PROJECT_ID not in project_ids:
        return

    if CAPSTONE_PROJECT_ID not in match.get("selected_project_ids", []):
        match.setdefault("selected_project_ids", []).append(CAPSTONE_PROJECT_ID)
        match["selected_project_ids"] = [
            p.id for p in resume.projects
            if p.id in set(match["selected_project_ids"])
        ]

    if CAPSTONE_AWARD_BULLET_ID in available_texts:
        bullets[CAPSTONE_AWARD_BULLET_ID] = available_texts[CAPSTONE_AWARD_BULLET_ID]
        if CAPSTONE_AWARD_BULLET_ID not in match.get("selected_bullet_ids", []):
            match.setdefault("selected_bullet_ids", []).append(
                CAPSTONE_AWARD_BULLET_ID
            )


def _best_package_to_minimum(
    *,
    resume: ResumeData,
    match: dict,
    item_id: str,
    bullets: dict[str, str],
    available_texts: dict[str, str],
    min_bullets: int,
    max_bullets: int,
    min_lines: int,
    max_lines: int,
) -> list[str]:
    """
    Return source bullets to add until an existing/new item meets BOTH minima.
    """
    scores = bullet_score_map(match)
    active = [bid for bid in bullets_for_item(resume, item_id) if bid in bullets]
    line_total = sum(estimated_lines(bullets[bid]) for bid in active)

    if len(active) >= min_bullets and line_total >= min_lines:
        return []

    candidates = [
        bid
        for bid in bullets_for_item(resume, item_id)
        if bid not in bullets and bid in available_texts
    ]
    candidates.sort(key=lambda bid: scores.get(bid, 0), reverse=True)

    package: list[str] = []
    for bid in candidates:
        if len(active) + len(package) >= max_bullets:
            break

        trial_lines = line_total + sum(
            estimated_lines(available_texts[x]) for x in package
        ) + estimated_lines(available_texts[bid])

        if trial_lines > max_lines:
            continue

        package.append(bid)

        if (
            len(active) + len(package) >= min_bullets
            and trial_lines >= min_lines
        ):
            break

    return package


def enforce_item_minimums(
    resume: ResumeData,
    match: dict,
    bullets: dict[str, str],
    available_texts: dict[str, str],
) -> list[str]:
    """
    Bring every selected experience/project up to its minimum bullet + line
    requirement using the strongest remaining truthful source bullets.
    """
    added: list[str] = []
    ensure_mandatory_capstone(resume, match, bullets, available_texts)

    for exp_id in list(match.get("selected_experience_ids", [])):
        package = _best_package_to_minimum(
            resume=resume,
            match=match,
            item_id=exp_id,
            bullets=bullets,
            available_texts=available_texts,
            **experience_limits(exp_id),
        )
        for bid in package:
            bullets[bid] = available_texts[bid]
            if bid not in match.get("selected_bullet_ids", []):
                match.setdefault("selected_bullet_ids", []).append(bid)
            added.append(bid)

    for project_id in list(match.get("selected_project_ids", [])):
        package = _best_package_to_minimum(
            resume=resume,
            match=match,
            item_id=project_id,
            bullets=bullets,
            available_texts=available_texts,
            **project_limits(project_id),
        )
        for bid in package:
            bullets[bid] = available_texts[bid]
            if bid not in match.get("selected_bullet_ids", []):
                match.setdefault("selected_bullet_ids", []).append(bid)
            added.append(bid)

    # Award wording is mandatory and exact.
    ensure_mandatory_capstone(resume, match, bullets, available_texts)
    return added


def drop_candidate_packages(
    resume: ResumeData,
    match: dict,
    bullets: dict[str, str],
) -> list[list[str]]:
    """
    Return removal packages that preserve hard minima, ranked by section balance.

    Secondary priority (also the fallback for selections without a balance policy):
    1. trim optional project depth,
    2. remove a whole low-value non-capstone project above MIN_PROJECTS,
    3. trim optional experience depth,
    4. remove a whole low-value experience above MIN_EXPERIENCES.
    """
    bullet_scores = bullet_score_map(match)
    item_scores = item_score_map(match)
    mapping = item_for_bullet(resume)
    types = item_type_map(resume)
    exp_counts, proj_counts = current_section_counts(resume, bullets)
    exp_lines, proj_lines = section_line_counts(resume, bullets)

    active_exp = [item_id for item_id, n in exp_counts.items() if n > 0]
    active_proj = [item_id for item_id, n in proj_counts.items() if n > 0]

    candidates: list[tuple[int, float, list[str]]] = []

    # Individual optional bullets.
    for bid, text in bullets.items():
        item_id = mapping.get(bid)
        if not item_id:
            continue

        # Never remove the capstone award bullet.
        if bid == CAPSTONE_AWARD_BULLET_ID:
            continue

        bscore = float(bullet_scores.get(bid, 50))
        iscore = float(item_scores.get(item_id, 50))
        lines = estimated_lines(text)

        if types.get(item_id) == "project":
            bscore = project_selection_score(item_id, bscore)
            iscore = project_selection_score(item_id, iscore)
            remaining_count = proj_counts[item_id] - 1
            remaining_lines = proj_lines[item_id] - lines
            limits = project_limits(item_id)
            if (
                remaining_count >= limits["min_bullets"]
                and remaining_lines >= limits["min_lines"]
            ):
                candidates.append((0, bscore + iscore * 0.10, [bid]))
        else:
            remaining_count = exp_counts[item_id] - 1
            remaining_lines = exp_lines[item_id] - lines
            limits = experience_limits(item_id)
            if (
                remaining_count >= limits["min_bullets"]
                and remaining_lines >= limits["min_lines"]
            ):
                candidates.append((2, bscore + iscore * 0.10, [bid]))

    # Whole optional projects.
    if len(active_proj) > MIN_PROJECTS:
        for project_id in active_proj:
            if project_id == CAPSTONE_PROJECT_ID:
                continue
            ids = [bid for bid in bullets_for_item(resume, project_id) if bid in bullets]
            if not ids:
                continue
            value = project_selection_score(project_id, item_scores.get(project_id, 50))
            candidates.append((1, value, ids))

    # Whole optional experiences.
    if len(active_exp) > MIN_EXPERIENCES:
        for exp_id in active_exp:
            ids = [bid for bid in bullets_for_item(resume, exp_id) if bid in bullets]
            if not ids:
                continue
            value = float(item_scores.get(exp_id, 50))
            candidates.append((3, value, ids))

    def priority(row):
        remaining = {bid: text for bid, text in bullets.items() if bid not in row[2]}
        return (content_balance_distance(resume, match, remaining), row[0], row[1], len(row[2]))

    candidates.sort(key=priority)
    return [row[2] for row in candidates]


def pick_drop_candidate(resume: ResumeData, match: dict, bullets: dict[str, str]) -> list[str] | None:
    packages = drop_candidate_packages(resume, match, bullets)
    return packages[0] if packages else None


def eligible_fill_candidates(
    resume: ResumeData,
    match: dict,
    active_bullets: dict[str, str],
    rejected: set[str],
    available_texts: dict[str, str] | None = None,
) -> list[str]:
    """
    Rank unused bullets for page filling while respecting per-item maxima.
    New items are added later as minimum-valid packages.
    """
    bullet_scores = bullet_score_map(match)
    item_scores = item_score_map(match)
    mapping = item_for_bullet(resume)
    types = item_type_map(resume)
    master = resume.all_bullets()
    available_texts = available_texts if available_texts is not None else {
        bid: bullet.text for bid, bullet in master.items()
    }
    policy = match.get("section_balance_policy")
    current_distance = content_balance_distance(resume, match, active_bullets)

    exp_counts, proj_counts = current_section_counts(resume, active_bullets)
    exp_lines, proj_lines = section_line_counts(resume, active_bullets)

    exp_total = sum(exp_counts.values())
    proj_total = sum(proj_counts.values())
    active_exp_count = sum(1 for n in exp_counts.values() if n > 0)
    active_project_count = sum(1 for n in proj_counts.values() if n > 0)

    ranked = []
    for bid, bullet in master.items():
        if bid in active_bullets or bid in rejected:
            continue
        if bid not in available_texts:
            continue

        # Award is handled as a mandatory exact-source bullet.
        if bid == CAPSTONE_AWARD_BULLET_ID:
            continue

        bscore = bullet_scores.get(bid, 0)
        if bscore < FILL_MIN_BULLET_SCORE:
            continue

        item_id = mapping.get(bid)
        if not item_id:
            continue

        item_type = types.get(item_id)
        iscore = item_scores.get(item_id, 0)
        lines = estimated_lines(available_texts[bid])

        if item_type == "experience":
            count = exp_counts[item_id]
            current_lines = exp_lines[item_id]
            new_item = count == 0
            limits = experience_limits(item_id)

            if new_item and active_exp_count >= MAX_EXPERIENCES:
                continue
            if not new_item:
                if count >= limits["max_bullets"]:
                    continue
                if current_lines + lines > limits["max_lines"]:
                    continue
            if exp_total >= MAX_EXPERIENCE_BULLETS:
                continue

            if new_item and active_exp_count < MAX_EXPERIENCES:
                phase = 72
            elif exp_total < TARGET_EXPERIENCE_BULLETS:
                phase = 68
            else:
                phase = 38

        else:
            # Apply the same small preference to cross-project bullet choices.
            # Eligibility above uses raw relevance, so low priorities never
            # filter out otherwise eligible content.
            bscore = project_selection_score(item_id, bscore)
            iscore = project_selection_score(item_id, iscore)
            count = proj_counts[item_id]
            current_lines = proj_lines[item_id]
            new_item = count == 0
            limits = project_limits(item_id)

            if new_item and active_project_count >= MAX_PROJECTS:
                continue
            if not new_item:
                if count >= limits["max_bullets"]:
                    continue
                if current_lines + lines > limits["max_lines"]:
                    continue
            if proj_total >= MAX_PROJECT_BULLETS:
                continue

            # Capstone technical depth receives a small priority boost.
            if item_id == CAPSTONE_PROJECT_ID:
                phase = 58
            elif new_item:
                phase = 52
            elif proj_total < TARGET_PROJECT_BULLETS:
                phase = 46
            else:
                phase = 24

        distance = 0.0
        if policy:
            package = fill_package_for_bullet(resume, match, active_bullets, available_texts, bid)
            trial = {**active_bullets, **{x: available_texts[x] for x in package}}
            distance = content_balance_distance(resume, match, trial)
            # Optional filler should not make an already imbalanced page worse.
            # Mandatory content/minima can still leave the final ratio outside
            # the target; this preference is not a hard validation rule.
            if distance > max(policy["tolerance"], current_distance) + 1e-9:
                continue

        priority = (
            -distance,
            phase,
            bscore,
            iscore,
            -lines,
            -len(bullet.text),
        )
        ranked.append((priority, bid))

    ranked.sort(reverse=True)
    return [bid for _, bid in ranked]



def selected_plain_text(resume: ResumeData, match: dict, bullets: dict[str, str]) -> str:
    parts = []
    for exp in resume.experience:
        if exp.id in set(match.get("selected_experience_ids", [])):
            parts += [exp.company, exp.title]
    for project in resume.projects:
        if project.id in set(match.get("selected_project_ids", [])):
            parts += [project.name, project.subtitle]
    parts += list(match.get("selected_skills", []))
    parts += list(bullets.values())
    return "\n".join(parts)


def ensure_item_selected_for_bullet(resume: ResumeData, match: dict, bullet_id: str) -> None:
    mapping = item_for_bullet(resume)
    item_id = mapping.get(bullet_id)
    if not item_id:
        return

    exp_ids = {exp.id for exp in resume.experience}
    proj_ids = {proj.id for proj in resume.projects}

    if item_id in exp_ids and item_id not in match.get("selected_experience_ids", []):
        match.setdefault("selected_experience_ids", []).append(item_id)
        # Restore master chronology.
        match["selected_experience_ids"] = [
            exp.id for exp in resume.experience
            if exp.id in set(match["selected_experience_ids"])
        ]

    if item_id in proj_ids and item_id not in match.get("selected_project_ids", []):
        match.setdefault("selected_project_ids", []).append(item_id)
        match["selected_project_ids"] = [
            proj.id for proj in resume.projects
            if proj.id in set(match["selected_project_ids"])
        ]


def compile_current(
    tex_path: Path,
    resume: ResumeData,
    match: dict,
    bullets: dict[str, str],
) -> tuple[Path | None, int | None, str]:
    tex_path.write_text(
        render_resume(TEMPLATE, resume, match, bullets),
        encoding="utf-8",
    )
    pdf_path, compile_error = compile_tex(tex_path)
    if not pdf_path:
        return None, None, compile_error
    return pdf_path, page_count(pdf_path), ""


def fill_package_for_bullet(
    resume: ResumeData,
    match: dict,
    bullets: dict[str, str],
    available_texts: dict[str, str],
    bid: str,
) -> list[str]:
    item_id = item_for_bullet(resume)[bid]
    if any(x in bullets for x in bullets_for_item(resume, item_id)):
        return [bid]
    if item_type_map(resume)[item_id] == "experience":
        limits = experience_limits(item_id)
    else:
        limits = project_limits(item_id)
    package = [bid] + _best_package_to_minimum(
        resume=resume, match=match, item_id=item_id,
        bullets={**bullets, bid: available_texts[bid]},
        available_texts=available_texts, **limits,
    )
    return list(dict.fromkeys(package))


def greedily_fill_one_page(
    *,
    job_name: str,
    tex_path: Path,
    resume: ResumeData,
    match: dict,
    bullets: dict[str, str],
    available_texts: dict[str, str],
) -> tuple[dict[str, str], int | None, list[str]]:
    """
    Maximize useful content without violating any hard item-level constraints.

    New experience/project entries are trialed as complete minimum-valid
    packages rather than one bullet at a time.
    """
    warnings: list[str] = []

    _, pages, compile_error = compile_current(tex_path, resume, match, bullets)
    if pages is None:
        warnings.append("Could not run adaptive page fill: " + compile_error)
        return bullets, None, warnings
    if pages != 1:
        return bullets, pages, warnings

    rejected: set[str] = set()
    trials = 0
    mapping = item_for_bullet(resume)

    while trials < MAX_FILL_TRIALS:
        candidates = eligible_fill_candidates(
            resume, match, bullets, rejected, available_texts
        )
        if not candidates:
            break

        bid = candidates[0]
        trials += 1
        if bid not in available_texts:
            rejected.add(bid)
            continue

        item_id = mapping.get(bid)
        package = fill_package_for_bullet(resume, match, bullets, available_texts, bid)

        snapshot_exp = list(match.get("selected_experience_ids", []))
        snapshot_proj = list(match.get("selected_project_ids", []))
        snapshot_ids = list(match.get("selected_bullet_ids", []))

        for add_bid in package:
            bullets[add_bid] = available_texts[add_bid]
            ensure_item_selected_for_bullet(resume, match, add_bid)
            if add_bid not in match.get("selected_bullet_ids", []):
                match.setdefault("selected_bullet_ids", []).append(add_bid)

        ensure_mandatory_capstone(resume, match, bullets, available_texts)
        violations = layout_constraint_violations(resume, match, bullets)

        # A new package may still be temporarily invalid only if another selected
        # item was already invalid, which should never happen after minimum
        # enforcement. Reject any trial that introduces a hard violation.
        trial_pages, trial_error = None, ""
        if not violations:
            _, trial_pages, trial_error = compile_current(
                tex_path, resume, match, bullets
            )

        if trial_pages == 1 and not violations:
            print(
                f"[FILL]    {job_name}: kept {', '.join(package)} ({item_id})"
            )
            continue

        for add_bid in package:
            if add_bid != CAPSTONE_AWARD_BULLET_ID:
                bullets.pop(add_bid, None)
        match["selected_experience_ids"] = snapshot_exp
        match["selected_project_ids"] = snapshot_proj
        match["selected_bullet_ids"] = snapshot_ids
        ensure_mandatory_capstone(resume, match, bullets, available_texts)
        rejected.update(package)

        if trial_pages is None and trial_error:
            warnings.append(
                f"Fill trial for {', '.join(package)} could not compile: "
                + trial_error
            )

    _, pages, final_error = compile_current(tex_path, resume, match, bullets)
    if pages is None and final_error:
        warnings.append("Final adaptive-fill compile failed: " + final_error)

    return bullets, pages, warnings


def rebalance_one_page(
    *, job_name: str, tex_path: Path, resume: ResumeData, match: dict,
    bullets: dict[str, str], available_texts: dict[str, str],
) -> tuple[dict[str, str], int | None, list[str]]:
    """Try legal swaps after filling, so a full page can still approach its target.

    Relevance chooses between equally balanced options. Existing hard limits,
    factual source text, and actual PDF page count remain authoritative.
    """
    policy = match.get("section_balance_policy")
    if not policy or layout_constraint_violations(resume, match, bullets):
        return bullets, 1, []
    mapping = item_for_bullet(resume)
    types = item_type_map(resume)
    scores = bullet_score_map(match)
    trials = 0
    warnings: list[str] = []
    touched_pdf = False

    def value(active):
        return sum(
            project_selection_score(mapping[bid], scores.get(bid, 0))
            if types[mapping[bid]] == "project" else scores.get(bid, 0)
            for bid in active
        )

    while trials < MAX_BALANCE_TRIALS:
        current_distance = content_balance_distance(resume, match, bullets)
        if current_distance <= policy["tolerance"]:
            break
        exp_lines, project_lines = section_totals(resume, bullets)
        over = "experience" if exp_lines / (exp_lines + project_lines) > policy["experience_target"] else "project"
        options = []
        seen = set()
        original_lines = exp_lines + project_lines

        def consider(trial_match, active, swap):
            key = tuple(sorted(active))
            if key in seen or layout_constraint_violations(resume, trial_match, active):
                return
            seen.add(key)
            distance = content_balance_distance(resume, trial_match, active)
            if distance + 1e-9 < current_distance:
                lost_lines = max(0, original_lines - sum(section_totals(resume, active)))
                options.append((0 if swap else 1, lost_lines, distance, -value(active), trial_match, active))

        for drop_ids in drop_candidate_packages(resume, match, bullets):
            if any(types[mapping[bid]] != over for bid in drop_ids):
                continue
            reduced = {bid: text for bid, text in bullets.items() if bid not in drop_ids}
            reduced_match = copy.deepcopy(match)
            sync_selected_items(resume, reduced_match, reduced)
            consider(reduced_match, reduced, False)
            for bid in eligible_fill_candidates(resume, reduced_match, reduced, set(drop_ids), available_texts):
                if types[mapping[bid]] == over:
                    continue
                package = fill_package_for_bullet(resume, reduced_match, reduced, available_texts, bid)
                active = {**reduced, **{x: available_texts[x] for x in package}}
                trial_match = copy.deepcopy(reduced_match)
                trial_match["selected_bullet_ids"] = list(active)
                for added_id in package:
                    ensure_item_selected_for_bullet(resume, trial_match, added_id)
                consider(trial_match, active, True)

            # A whole entry can free several lines. Consider replacing that
            # space with several bullets, rather than always trading it for one.
            growing, growing_match = reduced, reduced_match
            rejected = set(drop_ids)
            while sum(section_totals(resume, growing)) < original_lines:
                choices = eligible_fill_candidates(
                    resume, growing_match, growing, rejected, available_texts
                )
                choices = [bid for bid in choices if types[mapping[bid]] != over]
                if not choices:
                    break
                bid = choices[0]
                package = fill_package_for_bullet(resume, growing_match, growing, available_texts, bid)
                active = {**growing, **{x: available_texts[x] for x in package}}
                trial_match = copy.deepcopy(growing_match)
                trial_match["selected_bullet_ids"] = list(active)
                for added_id in package:
                    ensure_item_selected_for_bullet(resume, trial_match, added_id)
                if layout_constraint_violations(resume, trial_match, active):
                    rejected.update(package)
                    continue
                consider(trial_match, active, True)
                growing, growing_match = active, trial_match

        accepted = False
        for _, _, distance, _, trial_match, active in sorted(options, key=lambda row: row[:4]):
            if trials >= MAX_BALANCE_TRIALS:
                break
            trials += 1
            touched_pdf = True
            _, pages, error = compile_current(tex_path, resume, trial_match, active)
            if pages == 1:
                bullets = active
                match.clear()
                match.update(trial_match)
                print(f"[BALANCE] {job_name}: target gap {current_distance:.1%} -> {distance:.1%}")
                accepted = True
                break
            if error:
                warnings.append("Balance trial could not compile: " + error)
        if not accepted:
            break

    if touched_pdf:
        # Failed trials must never leave their PDF or TeX as the final artifact.
        _, pages, error = compile_current(tex_path, resume, match, bullets)
        if error:
            warnings.append("Could not restore final balanced PDF: " + error)
        return bullets, pages, warnings
    return bullets, 1, warnings


async def process_job(
    job_path: Path,
    resume: ResumeData,
    miniveil_profile: str | None = None,
) -> tuple[str, bool, float | None]:
    job_name = job_path.stem
    resume, job_text, miniveil = prepare_job_resume(
        resume, job_path.read_text(encoding="utf-8"), miniveil_profile
    )
    out_dir = OUTPUT_DIR / job_name
    out_dir.mkdir(parents=True, exist_ok=True)

    usages: list[UsageRecord] = []
    warnings: list[str] = []

    print(f"[ANALYZE] {job_name}")
    if miniveil:
        print(f"[PROFILE] {job_name} | Miniveil: {miniveil['profile']} ({miniveil['selected_by']})")
    analysis, usage = await analyze_job(job_text, job_name)
    usages.append(usage)
    write_json(out_dir / "job_analysis.json", analysis)

    print(f"[MATCH]   {job_name}")
    match, usage, match_warnings = await match_resume(resume, analysis)
    usages.append(usage)
    warnings.extend(match_warnings)

    match["miniveil_profile"] = miniveil
    match["section_balance_policy"] = balance_policy(analysis)
    # Conservative seed selection BEFORE rewriting. The PDF-aware fill stage can add more later.
    match = apply_initial_content_budget(resume, match)

    write_json(out_dir / "match.json", match)

    print(f"[REWRITE] {job_name}")
    bullets, usage = await rewrite_selected(resume, analysis, match)
    if usage:
        usages.append(usage)

    bullets, length_usages, length_warnings = await fix_lengths(
        resume, analysis, bullets
    )
    usages.extend(length_usages)
    warnings.extend(length_warnings)

    # Strict truth pass after all normal rewrites/length edits.
    print(f"[VERIFY]  {job_name}")
    support, unsupported, usage = await truth_check(resume, bullets)
    if usage:
        usages.append(usage)

    # Truth beats formatting: revert unsupported bullets to the original source.
    master_bullets = resume.all_bullets()
    reverted_ids: set[str] = set()
    for sid, ok in support.items():
        if not ok and sid in master_bullets:
            bullets[sid] = master_bullets[sid].text
            reverted_ids.add(sid)
            warnings.append(
                f"{sid} was reverted because the factual validator found unsupported claims: "
                + "; ".join(unsupported.get(sid, []))
            )

    # If a reverted original has a bad length, allow one targeted repair and re-check it.
    if reverted_ids:
        bullets, repair_usages, repair_warnings = await fix_lengths(
            resume, analysis, bullets, only_ids=reverted_ids
        )
        usages.extend(repair_usages)
        warnings.extend(repair_warnings)

        support2, unsupported2, usage2 = await truth_check(
            resume, bullets, ids=reverted_ids
        )
        if usage2:
            usages.append(usage2)

        for sid in reverted_ids:
            if not support2.get(sid, True):
                bullets[sid] = master_bullets[sid].text
                warnings.append(
                    f"{sid} failed the second truth check and was restored exactly "
                    "to its master-resume wording."
                )

    # Preserve every prepared bullet text so a bullet removed during page fitting
    # can still be reconsidered later. Any bullet never rewritten uses its exact
    # source wording during the adaptive fill stage.
    available_texts = {
        bid: bullet.text for bid, bullet in resume.all_bullets().items()
    }
    available_texts.update(bullets)

    # Rewrites can change item density. Re-enforce every hard minimum using
    # CURRENT wording before the first PDF compile.
    ensure_mandatory_capstone(resume, match, bullets, available_texts)
    added_for_minimums = enforce_item_minimums(
        resume,
        match,
        bullets,
        available_texts,
    )
    if added_for_minimums:
        warnings.append(
            "Added source bullets to satisfy per-item minimums: "
            + ", ".join(added_for_minimums)
        )

    # Never allow rewriting to alter the capstone award statement.
    if CAPSTONE_AWARD_BULLET_ID in available_texts:
        bullets[CAPSTONE_AWARD_BULLET_ID] = available_texts[
            CAPSTONE_AWARD_BULLET_ID
        ]

    # Render + compile.
    print(f"[COMPILE] {job_name}")
    tex_path = out_dir / "resume.tex"
    tex_path.write_text(
        render_resume(TEMPLATE, resume, match, bullets),
        encoding="utf-8",
    )
    pdf_path, compile_error = compile_tex(tex_path)

    pages: int | None = None
    if pdf_path:
        pages = page_count(pdf_path)
    else:
        warnings.append("PDF compile unavailable/failed: " + compile_error)

    # If over one page, first compress low-value two-line bullets, then drop only
    # the lowest-value bullet if compression still cannot fit.
    rounds = 0
    while pages is not None and pages > 1 and rounds < MAX_PAGE_FIX_ROUNDS:
        rounds += 1
        ids = pick_page_compression_ids(resume, match, bullets)

        if ids:
            print(f"[FIT {rounds}] {job_name}: compressing {', '.join(ids)}")
            candidate, usage = await compress_for_page(
                resume, analysis, bullets, ids
            )
            if usage:
                usages.append(usage)

            # Validate only the changed bullets before accepting them.
            changed = {sid for sid in ids if candidate.get(sid) != bullets.get(sid)}
            support3, unsupported3, usage3 = await truth_check(
                resume, candidate, ids=changed
            )
            if usage3:
                usages.append(usage3)

            for sid in changed:
                if not support3.get(sid, False):
                    warnings.append(
                        f"Rejected page-fit rewrite for {sid}: "
                        + "; ".join(unsupported3.get(sid, []))
                    )
                    continue

                trial_bullets = dict(bullets)
                trial_bullets[sid] = candidate[sid]
                if layout_constraint_violations(resume, match, trial_bullets):
                    warnings.append(
                        f"Rejected page-fit rewrite for {sid} because it would "
                        "violate an item bullet/line constraint."
                    )
                    continue

                bullets[sid] = candidate[sid]

        tex_path.write_text(
            render_resume(TEMPLATE, resume, match, bullets),
            encoding="utf-8",
        )
        pdf_path, compile_error = compile_tex(tex_path)
        if not pdf_path:
            warnings.append("PDF compile failed during page fitting: " + compile_error)
            pages = None
            break
        pages = page_count(pdf_path)

        if pages > 1:
            drop_ids = pick_drop_candidate(resume, match, bullets)
            if drop_ids is None:
                warnings.append(
                    "Still over one page and no constraint-safe content can be dropped."
                )
                break
            print(
                f"[FIT {rounds}] {job_name}: dropping "
                + ", ".join(drop_ids)
            )
            for drop_id in drop_ids:
                bullets.pop(drop_id, None)
            match["selected_bullet_ids"] = [
                x
                for x in match.get("selected_bullet_ids", [])
                if x not in set(drop_ids)
            ]
            sync_selected_items(resume, match, bullets)
            tex_path.write_text(
                render_resume(TEMPLATE, resume, match, bullets),
                encoding="utf-8",
            )
            pdf_path, compile_error = compile_tex(tex_path)
            if not pdf_path:
                warnings.append("PDF compile failed after dropping bullet: " + compile_error)
                pages = None
                break
            pages = page_count(pdf_path)

    # Once the resume fits, use actual LaTeX compilation to add more high-value
    # content until no further candidate can be added without spilling to page 2.
    if pages == 1:
        print(f"[FILL]    {job_name}: maximizing one-page content")
        bullets, pages, fill_warnings = greedily_fill_one_page(
            job_name=job_name,
            tex_path=tex_path,
            resume=resume,
            match=match,
            bullets=bullets,
            available_texts=available_texts,
        )
        warnings.extend(fill_warnings)
        sync_selected_items(resume, match, bullets)
        if pages == 1:
            bullets, pages, balance_warnings = rebalance_one_page(
                job_name=job_name, tex_path=tex_path, resume=resume, match=match,
                bullets=bullets, available_texts=available_texts,
            )
            warnings.extend(balance_warnings)
            sync_selected_items(resume, match, bullets)

    # Final truth status for report. Unmodified originals are inherently sourced;
    # rewritten bullets use the most recent validator results where available.
    final_support, final_unsupported, usage = await truth_check(resume, bullets)
    if usage:
        usages.append(usage)
    truth_report = {
        sid: {
            "supported": final_support.get(sid, False),
            "unsupported_claims": final_unsupported.get(sid, []),
        }
        for sid in bullets
    }

    qa = local_qa(bullets)
    plain_text = selected_plain_text(resume, match, bullets)
    coverage = keyword_coverage(analysis.get("priority_keywords", []), plain_text)

    # Final QA warnings.
    for sid, info in qa["bullet_lengths"].items():
        if info["status"] in {"inefficient_middle", "too_long"}:
            warnings.append(
                f"Final length warning: {sid} = {info['chars']} chars ({info['status']})."
            )

    if qa["duplicate_pairs"]:
        warnings.append("Potentially duplicate bullets detected; see qa.json.")

    if pages is not None and pages != 1:
        warnings.append(f"Final PDF is {pages} pages, not 1.")

    unsupported_final = [
        sid for sid, result in truth_report.items() if not result["supported"]
    ]
    if unsupported_final:
        # Exact master wording is the source of truth. Revert automatically
        # instead of leaving a known unsupported paraphrase in the final PDF.
        for sid in unsupported_final:
            if sid in master_bullets and sid in bullets:
                bullets[sid] = master_bullets[sid].text
                truth_report[sid] = {
                    "supported": True,
                    "unsupported_claims": [],
                }
                warnings.append(
                    f"{sid} was automatically restored to exact master-resume "
                    "wording after the final truth check."
                )

        ensure_mandatory_capstone(resume, match, bullets, available_texts)
        pdf_path, pages, final_compile_error = compile_current(
            tex_path, resume, match, bullets
        )
        if pages is None:
            warnings.append(
                "Compile failed after final truth repair: " + final_compile_error
            )

        # If an exact-source restoration pushed the document over one page,
        # remove the lowest-value content until the invariant is restored.
        while pages is not None and pages > 1:
            drop_ids = pick_drop_candidate(resume, match, bullets)
            if drop_ids is None:
                break
            warnings.append(
                "Dropped "
                + ", ".join(drop_ids)
                + " after truth restoration to preserve one page."
            )
            for drop_id in drop_ids:
                bullets.pop(drop_id, None)
            match["selected_bullet_ids"] = [
                x
                for x in match.get("selected_bullet_ids", [])
                if x not in set(drop_ids)
            ]
            sync_selected_items(resume, match, bullets)
            pdf_path, pages, final_compile_error = compile_current(
                tex_path, resume, match, bullets
            )

        unsupported_final = []

    # Final hard-layout validation.
    ensure_mandatory_capstone(resume, match, bullets, available_texts)
    sync_selected_items(resume, match, bullets)
    layout_violations = layout_constraint_violations(resume, match, bullets)
    for violation in layout_violations:
        warnings.append("Layout constraint violation: " + violation)

    # Persist the final selection and QA, including any page-fit changes or
    # exact-source restorations, rather than leaving match.json at its seed.
    qa = local_qa(bullets)
    coverage = keyword_coverage(
        analysis.get("priority_keywords", []), selected_plain_text(resume, match, bullets)
    )
    match["section_balance"] = balance_summary(resume, match, bullets)
    write_json(out_dir / "match.json", match)

    write_json(
        out_dir / "qa.json",
        {
            "local_qa": qa,
            "truth": truth_report,
            "keyword_coverage": coverage,
            "pages": pages,
            "layout_violations": layout_violations,
            "section_balance": match["section_balance"],
            "miniveil_profile": miniveil,
            "warnings": warnings,
            "usage": [u.as_dict() for u in usages],
        },
    )

    write_report(
        out_dir / "report.txt",
        job_name=job_name,
        analysis=analysis,
        match=match,
        resume=resume,
        bullets=bullets,
        qa=qa,
        truth=truth_report,
        keyword_coverage=coverage,
        pages=pages,
        usages=usages,
        warnings=warnings,
    )

    cleanup_aux(out_dir)
    cost = total_cost(usages)
    cost_text = "unknown" if cost is None else f"${cost:.4f}"
    ok = (
        pages == 1
        and not unsupported_final
        and not layout_violations
    )

    print(
        f"[DONE]    {job_name} | pages={pages if pages is not None else 'N/A'} "
        f"| match={match.get('overall_match_score', 'N/A')} "
        f"| est cost={cost_text}"
    )
    return job_name, ok, cost


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--job",
        help="Optional job filename/stem to process, e.g. databricks or databricks.txt",
    )
    parser.add_argument(
        "--miniveil", type=str.lower, choices=MINIVEIL_PROFILES,
        help="Miniveil title/bullet pool for this run; overrides job headers. Default: software.",
    )
    args = parser.parse_args()

    resume = load_resume()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.job:
        candidate = JOBS_DIR / args.job
        if candidate.suffix.lower() not in {".txt", ".md"}:
            txt = JOBS_DIR / f"{args.job}.txt"
            md = JOBS_DIR / f"{args.job}.md"
            candidate = txt if txt.exists() else md
        job_files = [candidate]
    else:
        job_files = sorted(list(JOBS_DIR.glob("*.txt")) + list(JOBS_DIR.glob("*.md")))

    job_files = [x for x in job_files if x.exists()]
    if not job_files:
        print("No job descriptions found in jobs/.")
        return

    print(f"Found {len(job_files)} job(s).")
    results = await asyncio.gather(
        *(process_job(job, resume, args.miniveil) for job in job_files),
        return_exceptions=True,
    )

    total_known_cost = 0.0
    known_cost_jobs = 0
    successes = 0

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)

    for result in results:
        if isinstance(result, Exception):
            print(f"[ERROR] {type(result).__name__}: {result}")
            continue

        name, ok, cost = result
        successes += int(ok)
        if cost is not None:
            total_known_cost += cost
            known_cost_jobs += 1
        print(f"{name}: {'OK' if ok else 'REVIEW'}")

    print(f"\nPassed final QA: {successes}/{len(job_files)}")
    if known_cost_jobs:
        print(f"Estimated API cost: ${total_known_cost:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
