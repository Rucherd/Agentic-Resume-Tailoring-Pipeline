"""Small, deterministic project preferences applied after AI relevance scoring."""

from config import (
    DEFAULT_PROJECT_PRIORITY,
    PROJECT_PRIORITIES,
    PROJECT_PRIORITY_WEIGHT,
)


def project_priority(project_id: str) -> float:
    return PROJECT_PRIORITIES.get(project_id, DEFAULT_PROJECT_PRIORITY)


def project_selection_score(project_id: str, relevance_score: float) -> float:
    """Blend a raw item/bullet relevance score with its project's soft priority.

    Call only for projects, always with an unadjusted relevance score. This is
    a ranking signal, never an eligibility threshold or a replacement for the
    model's reported relevance. The monotonic blend preserves bullet ordering
    within each project.
    """
    return round(
        (1.0 - PROJECT_PRIORITY_WEIGHT) * relevance_score
        + PROJECT_PRIORITY_WEIGHT * project_priority(project_id) * 10,
        4,
    )
