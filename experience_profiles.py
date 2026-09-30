"""Choose the Miniveil title and source pool before any AI or page fitting."""

import re

import config
from resume_models import ResumeData


MINIVEIL_PROFILES = ("software", "firmware")


def normalize_miniveil_profile(value: str) -> str:
    profile = value.strip().lower()
    if profile not in MINIVEIL_PROFILES:
        raise ValueError(
            f"Unknown Miniveil profile {value!r}; use 'software' or 'firmware'."
        )
    return profile


def prepare_job_resume(
    master: ResumeData,
    job_text: str,
    override: str | None = None,
) -> tuple[ResumeData, str, dict]:
    """Resolve CLI > leading job header > default without mutating shared data.

    Only a leading metadata line is a preference. Mentions in a job posting
    never choose the title or expose another source pool to the AI.
    """
    lines = job_text.lstrip("\ufeff").splitlines(keepends=True)
    header_profile = None
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        header = re.fullmatch(r"\s*Miniveil\s*:\s*(.*?)\s*", line, re.I)
        if header:
            header_profile = normalize_miniveil_profile(header.group(1))
            del lines[index]
        break

    if override is not None:
        profile, selected_by = normalize_miniveil_profile(override), "command line"
    elif header_profile is not None:
        profile, selected_by = header_profile, "job header"
    else:
        profile = normalize_miniveil_profile(config.DEFAULT_MINIVEIL_PROFILE)
        selected_by = "default"

    # Parallel jobs must never share a mutable title or active bullet list.
    resume = master.model_copy(deep=True)
    metadata = {}
    for entry in resume.experience:
        if entry.company.casefold() != "miniveil systems":
            continue
        if profile not in entry.profiles:
            raise ValueError(
                f"Miniveil Systems is missing the {profile!r} profile in "
                "resume_data.json. Add its title and bullets under profiles."
            )
        pool = entry.profiles[profile]
        ids = [bullet.id for bullet in pool.bullets]
        other_ids = {
            bullet.id
            for item in [*resume.experience, *resume.projects]
            if item is not entry
            for bullet in item.bullets
        }
        if len(ids) != len(set(ids)) or set(ids) & other_ids:
            raise ValueError(f"Miniveil {profile!r} contains duplicate bullet IDs.")
        entry.title = pool.title
        entry.bullets = pool.bullets
        entry.profiles = {}  # Downstream stages receive only the active pool.
        metadata = {
            "experience_id": entry.id,
            "profile": profile,
            "title": entry.title,
            "selected_by": selected_by,
        }
    return resume, "".join(lines), metadata
