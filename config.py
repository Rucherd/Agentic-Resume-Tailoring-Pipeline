import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOBS_DIR = ROOT / "jobs"
OUTPUT_DIR = ROOT / "output"
CACHE_DIR = ROOT / "cache"
RESUME_DATA = ROOT / "resume_data.json"
TEMPLATE = ROOT / "template.tex"

# Miniveil title and bullet pool. Override per job with a leading
# "Miniveil: firmware" line, or for a run with --miniveil firmware.
DEFAULT_MINIVEIL_PROFILE = "software"

# Hybrid defaults:
# - Terra handles analysis/matching/QA cheaply.
# - Sol handles wording, where quality matters most.
ANALYSIS_MODEL = os.getenv("ANALYSIS_MODEL", "gpt-5.6-terra")
MATCH_MODEL = os.getenv("MATCH_MODEL", "gpt-5.6-terra")
REWRITE_MODEL = os.getenv("REWRITE_MODEL", "gpt-5.6")
VALIDATION_MODEL = os.getenv("VALIDATION_MODEL", "gpt-5.6-terra")

ANALYSIS_EFFORT = os.getenv("ANALYSIS_EFFORT", "medium")
MATCH_EFFORT = os.getenv("MATCH_EFFORT", "medium")
REWRITE_EFFORT = os.getenv("REWRITE_EFFORT", "medium")
VALIDATION_EFFORT = os.getenv("VALIDATION_EFFORT", "low")

MAX_API_CONCURRENCY = int(os.getenv("MAX_API_CONCURRENCY", "5"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "4"))

# Richard's layout targets.
ONE_LINE_MAX = 105
TWO_LINE_MIN = 190
TWO_LINE_MAX = 210

# One-page packing policy.
#
# HARD SECTION RULES
#
# Experiences:
#   - 2 to 3 experience entries total.
#   - Defaults: 3-6 bullets AND 3-12 estimated bullet-text lines per experience.
#   - Employer-specific limits are listed in EXPERIENCE_LIMIT_OVERRIDES below.
#   - Never exceed 14 experience bullets; role-based line balance guides filling.
#
# Projects:
#   - 2 to 3 project entries total.
#   - Capstone (proj1) is mandatory.
#   - Capstone award bullet (proj1.b8) is mandatory and kept in exact source wording.
#   - EACH project: 2-4 bullets AND 2-5 estimated bullet-text lines.
#   - Capstone: at least 3 bullets including the award, up to 6 estimated lines.
#
# The compiled PDF must remain exactly one page.

MIN_EXPERIENCES = 2
MAX_EXPERIENCES = 3
INITIAL_EXPERIENCES = 3

MIN_BULLETS_PER_EXPERIENCE = 3
MAX_BULLETS_PER_EXPERIENCE = 6
MIN_EXPERIENCE_LINES = 3
MAX_EXPERIENCE_LINES = 10

# Fallback filling targets for selections without a section_balance_policy.
TARGET_EXPERIENCE_BULLETS = 11
MAX_EXPERIENCE_BULLETS = 14

# Employer-specific bullet and estimated line limits.
EXPERIENCE_LIMIT_OVERRIDES = {
    "exp1": {"min_bullets": 3, "min_lines": 3, "max_lines": 9},  # miniveil
    "exp2": {"min_bullets": 3, "min_lines": 5, "max_lines": 9},  # OTPP
    "exp3": {"max_bullets": 3, "min_lines": 3},  # TRREB
}


def experience_limits(experience_id: str) -> dict[str, int]:
    return {
        "min_bullets": MIN_BULLETS_PER_EXPERIENCE,
        "max_bullets": MAX_BULLETS_PER_EXPERIENCE,
        "min_lines": MIN_EXPERIENCE_LINES,
        "max_lines": MAX_EXPERIENCE_LINES,
        **EXPERIENCE_LIMIT_OVERRIDES.get(experience_id, {}),
    }


MIN_PROJECTS = 2
MAX_PROJECTS = 3
INITIAL_PROJECTS = 3

MIN_BULLETS_PER_PROJECT = 2
MAX_BULLETS_PER_PROJECT = 4
MIN_PROJECT_LINES = 2
MAX_PROJECT_LINES = 5

# Broad section-level guard. Per-project limits remain the real constraint.
TARGET_PROJECT_BULLETS = 6
MAX_PROJECT_BULLETS = 9

# Soft targets for estimated bullet-text space, excluding headings and Skills.
EXPERIENCE_SHARE_TARGETS = {
    "software": 0.65,
    "firmware_embedded": 0.70,
    "gpu_ai_compiler": 0.58,
}
SECTION_BALANCE_TOLERANCE = 0.03  # Allow rounding to whole bullets/lines.
MAX_BALANCE_TRIALS = 12

CAPSTONE_PROJECT_ID = "proj1"
CAPSTONE_AWARD_BULLET_ID = "proj1.b8"

# Three two-line capstone bullets must fit without shortening the award.
PROJECT_LIMIT_OVERRIDES = {
    CAPSTONE_PROJECT_ID: {"min_bullets": 3, "max_lines": 6},
}


def project_limits(project_id: str) -> dict[str, int]:
    return {
        "min_bullets": MIN_BULLETS_PER_PROJECT,
        "max_bullets": MAX_BULLETS_PER_PROJECT,
        "min_lines": MIN_PROJECT_LINES,
        "max_lines": MAX_PROJECT_LINES,
        **PROJECT_LIMIT_OVERRIDES.get(project_id, {}),
    }


# Soft preferences for selection; display order uses PROJECT_PRIORITIES directly.
# Ratings are out of 10, converted to the relevance score's 0-100 scale.
# No rating excludes a project; capstone inclusion is a separate rule.
PROJECT_PRIORITY_WEIGHT = 0.2
DEFAULT_PROJECT_PRIORITY = 7.5  # Fallback for newly added projects.
PROJECT_PRIORITIES = {
    "proj1": 10.0,  # V2I Shared Perception System (mandatory)
    "proj2": 9.0,   # CUDA-Accelerated LiDAR Voxelization
    "proj3": 8.5,   # GIS Mapping Engine
    "proj4": 8.5,   # Deep Learning Project
    "proj5": 5.0,   # Movie Catalogue Web App
    "proj7": 4.0,   # Portfolio Website
    "proj8": 7.5,   # Agentic Resume Tailoring Pipeline
}

# Skills section packing.
# Choose the most relevant categories, then fill each displayed line with
# additional truthful skills from that same category instead of leaving
# half-empty lines.
MAX_SKILL_LINES = 4
SKILL_LINE_MAX_CHARS = 100
SKILL_LINE_TARGET_MIN_CHARS = 88

# Project headings should stay compact so the date does not collide with the
# technology subtitle.
PROJECT_SUBTITLE_MAX_ITEMS = 3
PROJECT_SUBTITLE_MAX_CHARS = 34

# All truthful bullets may be considered as page filler. Relevance still
# controls priority, but a low score is not an automatic exclusion.
FILL_MIN_BULLET_SCORE = 0

# Safety cap on compile attempts while greedily filling the last page.
MAX_FILL_TRIALS = 60

# Page-fit policy.
MIN_BULLETS_PER_SELECTED_ITEM = 1
MAX_PAGE_FIX_ROUNDS = 12

# Conservative price estimates in USD per 1M tokens.
# These deliberately charge every input token at the regular input rate,
# so prompt-caching discounts, if any, are not assumed.
MODEL_PRICING = {
    "gpt-5.6": {"input": 4.00, "output": 20.00},
    "gpt-5.6-sol": {"input": 4.00, "output": 20.00},
    "gpt-5.6-terra": {"input": 2.00, "output": 12.00},
    "gpt-5.6-luna": {"input": 0.20, "output": 1.20},
}
