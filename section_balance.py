"""Role-specific preferences for experience versus project bullet-text space."""

import re

from config import EXPERIENCE_SHARE_TARGETS, SECTION_BALANCE_TOLERANCE
from resume_models import ResumeData


def balance_policy(analysis: dict) -> dict:
    profile = analysis.get("layout_profile")
    reason = "Job-analysis classification"
    if profile not in EXPERIENCE_SHARE_TARGETS:
        # Compatibility with saved analyses created before layout_profile existed.
        # Titles outrank broad role families; incidental keywords in preferred
        # skills (e.g. Copilot, GCC) must not reclassify a software role.
        profile = "software"
        reason = "Default software profile"
        for field in ("role_title", "role_family"):
            text = analysis.get(field, "")
            if re.search(r"\b(firmware|embedded|rtos|bare[- ]metal)\b", text, re.I):
                profile = "firmware_embedded"
            elif re.search(
                r"\b(gpu|cuda|compiler|compilers|ai|ml|llm|genai|machine learning|"
                r"artificial intelligence|deep learning|computer vision)\b", text, re.I
            ):
                profile = "gpu_ai_compiler"
            else:
                continue
            reason = f"Saved analysis {field}"
            break
    target = EXPERIENCE_SHARE_TARGETS[profile]
    return {
        "profile": profile,
        "experience_target": target,
        "project_target": round(1 - target, 4),
        "tolerance": SECTION_BALANCE_TOLERANCE,
        "basis": "estimated_bullet_text_lines",
        "reason": reason,
    }


def section_totals(resume: ResumeData, bullets: dict[str, str]) -> tuple[int, int]:
    def lines(entries):
        return sum(
            max(1, (len(bullets[b.id]) + 104) // 105)
            for entry in entries for b in entry.bullets if b.id in bullets
        )
    return lines(resume.experience), lines(resume.projects)


def balance_distance(policy: dict, experience_lines: int, project_lines: int) -> float:
    total = experience_lines + project_lines
    return abs(experience_lines / total - policy["experience_target"]) if total else 0.0


def content_balance_distance(resume: ResumeData, match: dict, bullets: dict[str, str]) -> float:
    policy = match.get("section_balance_policy")
    return balance_distance(policy, *section_totals(resume, bullets)) if policy else 0.0


def balance_summary(resume: ResumeData, match: dict, bullets: dict[str, str]) -> dict:
    policy = match.get("section_balance_policy")
    if not policy:
        return {}
    experience, projects = section_totals(resume, bullets)
    total = experience + projects
    return {
        **policy,
        "experience_lines": experience,
        "project_lines": projects,
        "experience_share": experience / total if total else 0.0,
        "project_share": projects / total if total else 0.0,
        "within_target_band": bool(total) and (
            balance_distance(policy, experience, projects) <= policy["tolerance"]
        ),
    }
