BOOTSTRAP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "personal": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "location": {"type": "string"},
                "linkedin": {"type": "string"},
                "github": {"type": "string"},
                "website": {"type": "string"},
            },
            "required": ["name", "email", "phone", "location", "linkedin", "github", "website"],
        },
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "institution": {"type": "string"},
                    "degree": {"type": "string"},
                    "location": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "details": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["institution", "degree", "location", "start", "end", "details"],
            },
        },
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "company": {"type": "string"},
                    "title": {"type": "string"},
                    "location": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["company", "title", "location", "start", "end", "bullets"],
            },
        },
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "date": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                    "skills": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "subtitle", "date", "bullets", "skills"],
            },
        },
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "category": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["category", "items"],
            },
        },
    },
    "required": ["personal", "education", "experience", "projects", "skills"],
}

JOB_ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "role_title": {"type": "string"},
        "role_family": {"type": "string"},
        "layout_profile": {
            "type": "string",
            "enum": ["software", "firmware_embedded", "gpu_ai_compiler"],
        },
        "seniority": {"type": "string"},
        "must_have": {"type": "array", "items": {"type": "string"}},
        "preferred": {"type": "array", "items": {"type": "string"}},
        "responsibilities": {"type": "array", "items": {"type": "string"}},
        "priority_keywords": {"type": "array", "items": {"type": "string"}},
        "hard_requirements": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": [
        "role_title", "role_family", "layout_profile", "seniority", "must_have", "preferred",
        "responsibilities", "priority_keywords", "hard_requirements", "summary"
    ],
}

MATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "overall_match_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "item_scores": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "item_id": {"type": "string"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "rationale": {"type": "string"},
                },
                "required": ["item_id", "score", "rationale"],
            },
        },
        "bullet_scores": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "bullet_id": {"type": "string"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "rationale": {"type": "string"},
                },
                "required": ["bullet_id", "score", "rationale"],
            },
        },
        "selected_experience_ids": {"type": "array", "items": {"type": "string"}},
        "selected_project_ids": {"type": "array", "items": {"type": "string"}},
        "selected_bullet_ids": {"type": "array", "items": {"type": "string"}},
        "rewrite_bullet_ids": {"type": "array", "items": {"type": "string"}},
        "selected_skills": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "selection_rationale": {"type": "string"},
    },
    "required": [
        "overall_match_score", "item_scores", "bullet_scores",
        "selected_experience_ids", "selected_project_ids",
        "selected_bullet_ids", "rewrite_bullet_ids",
        "selected_skills", "gaps", "selection_rationale"
    ],
}

REWRITE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rewritten_bullets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source_id": {"type": "string"},
                    "text": {"type": "string"},
                    "keywords_used": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["source_id", "text", "keywords_used"],
            },
        }
    },
    "required": ["rewritten_bullets"],
}

TRUTH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source_id": {"type": "string"},
                    "supported": {"type": "boolean"},
                    "unsupported_claims": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
                "required": ["source_id", "supported", "unsupported_claims", "notes"],
            },
        }
    },
    "required": ["checks"],
}

LENGTH_FIX_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fixed_bullets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source_id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["source_id", "text"],
            },
        }
    },
    "required": ["fixed_bullets"],
}
