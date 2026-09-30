from __future__ import annotations

from pathlib import Path
from jinja2 import Environment, BaseLoader

from resume_models import ResumeData
from project_priority import project_priority
from config import (
    MAX_SKILL_LINES,
    SKILL_LINE_MAX_CHARS,
    SKILL_LINE_TARGET_MIN_CHARS,
    PROJECT_SUBTITLE_MAX_ITEMS,
    PROJECT_SUBTITLE_MAX_CHARS,
    CAPSTONE_AWARD_BULLET_ID,
)


def latex_escape(text: str) -> str:
    # Escape one source character at a time so braces introduced by an escape
    # sequence are not escaped again on a later pass.
    mapping = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        # Raw angle brackets become inverted punctuation in OT1 text fonts.
        "<": r"\textless{}",
        ">": r"\textgreater{}",
    }
    return "".join(mapping.get(ch, ch) for ch in text)


def _header(resume: ResumeData) -> str:
    p = resume.personal
    pieces = []
    if p.phone:
        pieces.append(latex_escape(p.phone))
    if p.email:
        pieces.append(rf"\href{{mailto:{p.email}}}{{{latex_escape(p.email)}}}")
    if p.linkedin:
        pieces.append(rf"\href{{{p.linkedin}}}{{\underline{{LinkedIn}}}}")
    if p.github:
        pieces.append(rf"\href{{{p.github}}}{{\underline{{GitHub}}}}")
    if p.website:
        pieces.append(rf"\href{{{p.website}}}{{\underline{{Portfolio}}}}")

    return (
        r"\begin{center}" + "\n"
        + rf"{{\Huge \scshape {latex_escape(p.name)}}} \\ \vspace{{1pt}}" + "\n"
        + r"\small " + " $|$ ".join(pieces) + "\n"
        + r"\end{center}"
    )


def _education(resume: ResumeData) -> str:
    lines = [r"\section{Education}", r"\resumeSubHeadingListStart"]
    for edu in resume.education:
        lines.append(
            rf"\resumeSubheading"
            rf"{{{latex_escape(edu.institution)}}}{{{latex_escape(edu.location)}}}"
            rf"{{{latex_escape(edu.degree)}}}{{{latex_escape(edu.start)} -- {latex_escape(edu.end)}}}"
        )
        if edu.details:
            lines.append(r"\resumeItemListStart")
            for detail in edu.details:
                lines.append(rf"\resumeItem{{{latex_escape(detail)}}}")
            lines.append(r"\resumeItemListEnd")
    lines.append(r"\resumeSubHeadingListEnd")
    return "\n".join(lines)


def _experience(
    resume: ResumeData,
    selected_items: set[str],
    selected_bullets: set[str],
    final_bullets: dict[str, str],
) -> str:
    lines = [r"\section{Experience}", r"\resumeSubHeadingListStart"]
    for exp in resume.experience:  # master order is preserved
        if exp.id not in selected_items:
            continue
        item_bullets = [b for b in exp.bullets if b.id in selected_bullets and b.id in final_bullets]
        if not item_bullets:
            continue
        lines.append(
            rf"\resumeSubheading"
            rf"{{{latex_escape(exp.company)}}}{{{latex_escape(exp.start)} -- {latex_escape(exp.end)}}}"
            rf"{{{latex_escape(exp.title)}}}{{{latex_escape(exp.location)}}}"
        )
        lines.append(r"\resumeItemListStart")
        for bullet in item_bullets:
            lines.append(rf"\resumeItem{{{latex_escape(final_bullets[bullet.id])}}}")
        lines.append(r"\resumeItemListEnd")
    lines.append(r"\resumeSubHeadingListEnd")
    return "\n".join(lines)


def _compact_project_subtitle(project, selected_skills: list[str]) -> str:
    """Choose a short truthful technology subtitle that leaves room for the date."""
    rank = {skill: i for i, skill in enumerate(selected_skills)}
    skills = list(project.skills) if project.skills else [
        x.strip() for x in project.subtitle.split(",") if x.strip()
    ]

    # Job-relevant project technologies first; preserve project order for ties
    # and for technologies that are not part of the global skills taxonomy.
    indexed = list(enumerate(skills))
    indexed.sort(key=lambda pair: (rank.get(pair[1], 10_000), pair[0]))

    chosen: list[str] = []
    for _, skill in indexed:
        if len(chosen) >= PROJECT_SUBTITLE_MAX_ITEMS:
            break
        trial = ", ".join(chosen + [skill])
        if len(trial) <= PROJECT_SUBTITLE_MAX_CHARS:
            chosen.append(skill)

    if not chosen and skills:
        chosen = [skills[0]]

    return ", ".join(chosen)


def _projects(
    resume: ResumeData,
    selected_items: set[str],
    selected_bullets: set[str],
    final_bullets: dict[str, str],
    selected_skills: list[str],
) -> str:
    lines = [r"\section{Projects}", r"\resumeSubHeadingListStart"]
    for project in sorted(resume.projects, key=lambda p: project_priority(p.id), reverse=True):
        if project.id not in selected_items:
            continue
        item_bullets = [
            b for b in project.bullets
            if b.id in selected_bullets and b.id in final_bullets
        ]
        if not item_bullets:
            continue

        item_bullets.sort(key=lambda b: b.id != CAPSTONE_AWARD_BULLET_ID)

        title = latex_escape(project.name)
        subtitle_raw = _compact_project_subtitle(project, selected_skills)
        subtitle = latex_escape(subtitle_raw)
        heading = title if not subtitle else rf"{title} $|$ \emph{{{subtitle}}}"

        lines.append(
            rf"\resumeProjectHeading{{\textbf{{{heading}}}}}"
            rf"{{{latex_escape(project.date)}}}"
        )
        lines.append(r"\resumeItemListStart")
        for bullet in item_bullets:
            lines.append(
                rf"\resumeItem{{{latex_escape(final_bullets[bullet.id])}}}"
            )
        lines.append(r"\resumeItemListEnd")
    lines.append(r"\resumeSubHeadingListEnd")
    return "\n".join(lines)


def _skills(resume: ResumeData, selected_skills: list[str]) -> str:
    """
    Choose the most relevant skill categories, then FILL each displayed line.

    Relevance chooses the category and the first skills on the line. Once a
    category earns a line, truthful skills from that same master-resume category
    are used as compact filler until the line is close to the width budget.
    """
    if not resume.skills:
        return ""

    rank = {skill: i for i, skill in enumerate(selected_skills)}
    default_rank = len(selected_skills) + 1000

    category_rows = []
    for category_index, category in enumerate(resume.skills):
        relevant = [x for x in category.items if x in rank]

        # A category is primarily ranked by its best job-relevant skill.
        # Categories with no explicit match remain eligible as fallback.
        best_rank = min((rank[x] for x in relevant), default=default_rank + category_index)

        # Relevant skills first, then all remaining truthful skills in the
        # category's master-resume order.
        relevant_sorted = sorted(relevant, key=lambda x: rank[x])
        candidates = relevant_sorted + [
            x for x in category.items if x not in set(relevant_sorted)
        ]

        prefix = f"{category.category}: "
        chosen: list[str] = []

        for skill in candidates:
            trial = prefix + ", ".join(chosen + [skill])
            if len(trial) <= SKILL_LINE_MAX_CHARS:
                chosen.append(skill)

        if not chosen and candidates:
            chosen = [candidates[0]]

        plain = prefix + ", ".join(chosen)
        category_rows.append(
            (best_rank, category_index, len(plain), category.category, chosen)
        )

    category_rows.sort(key=lambda row: (row[0], row[1]))
    category_rows = category_rows[:MAX_SKILL_LINES]

    rendered = [
        rf"\textbf{{{latex_escape(category)}}}: "
        + ", ".join(latex_escape(x) for x in chosen)
        for _, _, _, category, chosen in category_rows
        if chosen
    ]

    if not rendered:
        return ""

    return "\n".join(
        [
            r"\section{Technical Skills}",
            r"\begin{itemize}[leftmargin=0.15in, label={}]",
            r"\small{\item{",
            r" \\ ".join(rendered),
            r"}}",
            r"\end{itemize}",
        ]
    )


def render_resume(
    template_path: Path,
    resume: ResumeData,
    match: dict,
    final_bullets: dict[str, str],
) -> str:
    template_text = template_path.read_text(encoding="utf-8")
    env = Environment(
        loader=BaseLoader(),
        autoescape=False,
        variable_start_string="<<",
        variable_end_string=">>",
        block_start_string="<%",
        block_end_string="%>",
        comment_start_string="<#",
        comment_end_string="#>",
    )
    template = env.from_string(template_text)

    selected_exp = set(match.get("selected_experience_ids", []))
    selected_proj = set(match.get("selected_project_ids", []))
    selected_bullets = set(match.get("selected_bullet_ids", []))

    return template.render(
        HEADER=_header(resume),
        EDUCATION=_education(resume),
        EXPERIENCE=_experience(
            resume, selected_exp, selected_bullets, final_bullets
        ),
        PROJECTS=_projects(
            resume,
            selected_proj,
            selected_bullets,
            final_bullets,
            match.get("selected_skills", []),
        ),
        SKILLS=_skills(resume, match.get("selected_skills", [])),
    )
