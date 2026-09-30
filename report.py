from __future__ import annotations

import json
from pathlib import Path

from openai_service import UsageRecord
from resume_models import ResumeData
from section_balance import balance_summary
from config import (
    MIN_EXPERIENCES,
    MAX_EXPERIENCES,
    experience_limits,
    MIN_PROJECTS,
    MAX_PROJECTS,
    project_limits,
    CAPSTONE_PROJECT_ID,
    CAPSTONE_AWARD_BULLET_ID,
)


def total_cost(usages: list[UsageRecord]) -> float | None:
    costs = [u.estimated_cost_usd for u in usages if u.estimated_cost_usd is not None]
    if not costs:
        return None
    return sum(costs)


def write_report(
    path: Path,
    *,
    job_name: str,
    analysis: dict,
    match: dict,
    resume: ResumeData,
    bullets: dict[str, str],
    qa: dict,
    truth: dict,
    keyword_coverage: dict,
    pages: int | None,
    usages: list[UsageRecord],
    warnings: list[str],
) -> None:
    lines = [
        f"JOB: {job_name}",
        f"ROLE: {analysis.get('role_title', '')}",
        f"MATCH SCORE: {match.get('overall_match_score', 'N/A')}/100",
        f"PDF PAGES: {pages if pages is not None else 'not compiled'}",
    ]
    miniveil = match.get("miniveil_profile")
    if miniveil:
        lines.append(
            f"MINIVEIL PROFILE: {miniveil['profile']} — {miniveil['title']} "
            f"(selected by {miniveil['selected_by']})"
        )
    lines += ["", "TOP REQUIREMENTS"]
    lines.extend(f"- {x}" for x in analysis.get("must_have", []))

    lines += ["", "GAPS"]
    gaps = match.get("gaps", [])
    if gaps:
        lines.extend(f"- {x}" for x in gaps)
    else:
        lines.append("- None identified")

    lines += [
        "",
        f"EXACT KEYWORD COVERAGE: {keyword_coverage.get('percent', 0)}%",
        "FOUND KEYWORDS:",
    ]
    lines.extend(f"- {x}" for x in keyword_coverage.get("found", []))
    lines += ["", "MISSING EXACT KEYWORDS:"]
    lines.extend(f"- {x}" for x in keyword_coverage.get("missing", []))

    lines += ["", "SELECTED ITEMS"]
    item_scores = {x["item_id"]: x["score"] for x in match.get("item_scores", [])}
    for item_id in match.get("selected_experience_ids", []) + match.get("selected_project_ids", []):
        lines.append(f"- {item_id}: {item_scores.get(item_id, 'N/A')}/100")

    project_rankings = match.get("project_rankings", [])
    if project_rankings:
        weight = match["project_priority_weight"]
        lines += [
            "",
            f"PROJECT PREFERENCES ({1 - weight:.0%} relevance / {weight:.0%} priority)",
            "Selection scores guide ranking; all projects remain eligible.",
        ]
        project_names = {p.id: p.name for p in resume.projects}
        selected_projects = set(match.get("selected_project_ids", []))
        for row in sorted(project_rankings, key=lambda r: r["selection_score"], reverse=True):
            item_id = row["item_id"]
            status = "selected" if item_id in selected_projects else "not selected"
            if row["mandatory"]:
                status += ", mandatory"
            lines.append(
                f"- {item_id} ({project_names.get(item_id, item_id)}): "
                f"priority={row['default_priority']:g}/10, "
                f"relevance={row['relevance_score']}/100, "
                f"selection={row['selection_score']:.2f}/100 [{status}]"
            )

    recommended_skills = match.get("recommended_skills", [])
    selected_skills = match.get("selected_skills", [])
    lines += [
        "",
        f"RANKED SKILL CANDIDATES: {len(selected_skills)}",
        f"SKILL CANDIDATES FROM MATCHER: {len(recommended_skills)}",
    ]

    lines += ["", "BULLET LENGTHS"]
    for sid, info in qa.get("bullet_lengths", {}).items():
        lines.append(f"- {sid}: {info['chars']} chars ({info['status']})")

    # Experience/project density summary.
    exp_ids = set(match.get("selected_experience_ids", []))
    project_ids = set(match.get("selected_project_ids", []))

    balance = balance_summary(resume, match, bullets)
    if balance:
        lines += [
            "", "EXPERIENCE / PROJECT BALANCE",
            f"Profile: {balance['profile']} ({balance['reason']})",
            "Basis: estimated bullet-text lines; excludes headings, Education, and Skills.",
            f"Target: {balance['experience_target']:.0%} experience / {balance['project_target']:.0%} projects",
            f"Actual: {balance['experience_share']:.1%} experience / {balance['project_share']:.1%} projects "
            f"({balance['experience_lines']} / {balance['project_lines']} estimated lines)",
            f"Within +/-{balance['tolerance'] * 100:g} percentage points of target: {'YES' if balance['within_target_band'] else 'NO'}",
            "Soft target; available content, hard item limits, and one-page fit take precedence.",
        ]

    lines += ["", "EXPERIENCE DENSITY"]
    for exp in resume.experience:
        if exp.id not in exp_ids:
            continue
        selected_ids = [b.id for b in exp.bullets if b.id in bullets]
        estimated = sum(
            max(1, (len(bullets[bid]) + 104) // 105)
            for bid in selected_ids
        )
        limits = experience_limits(exp.id)
        bullet_ok = limits["min_bullets"] <= len(selected_ids) <= limits["max_bullets"]
        line_ok = limits["min_lines"] <= estimated <= limits["max_lines"]
        status = "PASS" if bullet_ok and line_ok else "FAIL"
        lines.append(
            f"- {exp.id}: {len(selected_ids)} point(s), "
            f"~{estimated} line(s) [{status}]"
        )

    lines += ["", "PROJECT DENSITY"]
    for project in resume.projects:
        if project.id not in project_ids:
            continue
        selected_ids = [b.id for b in project.bullets if b.id in bullets]
        estimated = sum(
            max(1, (len(bullets[bid]) + 104) // 105)
            for bid in selected_ids
        )
        limits = project_limits(project.id)
        bullet_ok = limits["min_bullets"] <= len(selected_ids) <= limits["max_bullets"]
        line_ok = limits["min_lines"] <= estimated <= limits["max_lines"]
        mandatory = ""
        if project.id == CAPSTONE_PROJECT_ID:
            award_ok = CAPSTONE_AWARD_BULLET_ID in selected_ids
            mandatory = f", capstone_award={'YES' if award_ok else 'NO'}"
        status = "PASS" if bullet_ok and line_ok and (
            project.id != CAPSTONE_PROJECT_ID or CAPSTONE_AWARD_BULLET_ID in selected_ids
        ) else "FAIL"
        lines.append(
            f"- {project.id}: {len(selected_ids)} point(s), "
            f"~{estimated} line(s){mandatory} [{status}]"
        )

    exp_count_status = "PASS" if MIN_EXPERIENCES <= len(exp_ids) <= MAX_EXPERIENCES else "FAIL"
    proj_count_status = "PASS" if MIN_PROJECTS <= len(project_ids) <= MAX_PROJECTS else "FAIL"
    lines += [
        "",
        "SECTION COUNTS",
        f"- Experiences: {len(exp_ids)} ({MIN_EXPERIENCES}-{MAX_EXPERIENCES}) [{exp_count_status}]",
        f"- Projects: {len(project_ids)} ({MIN_PROJECTS}-{MAX_PROJECTS}) [{proj_count_status}]",
    ]

    lines += ["", "TRUTH CHECK"]
    for sid, info in truth.items():
        status = "PASS" if info.get("supported") else "FAIL"
        lines.append(f"- {sid}: {status}")
        for claim in info.get("unsupported_claims", []):
            lines.append(f"    unsupported: {claim}")

    lines += ["", "API USAGE"]
    for u in usages:
        cost = "unknown" if u.estimated_cost_usd is None else f"${u.estimated_cost_usd:.4f}"
        lines.append(
            f"- {u.stage}: {u.model}, in={u.input_tokens}, out={u.output_tokens}, est={cost}"
        )
    total = total_cost(usages)
    lines.append(f"TOTAL ESTIMATED API COST: {'unknown' if total is None else f'${total:.4f}'}")

    if warnings:
        lines += ["", "WARNINGS"]
        lines.extend(f"- {w}" for w in warnings)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
