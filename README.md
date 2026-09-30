# Agentic Resume Tailoring Pipeline

A personal engineering project that combines LLM orchestration, structured resume data, and Python validation to produce job-specific LaTeX resumes and PDFs.

> **Portfolio demonstration only. This repository is not intended for public use.** The workflow is highly personalized to my resume structure, content-selection preferences, experience profiles, and formatting rules. It is published to demonstrate the project's implementation and engineering decisions, not as a general-purpose application or a tool others can clone and use without substantial adaptation.

## What it does

The pipeline analyzes a job description, selects relevant material from a structured master resume, and selectively rewrites bullets. It checks the rewritten claims against their source text, renders a LaTeX document, and adjusts content to target a one-page PDF.

Each run also produces a report with relevance scores, exact-keyword coverage, formatting checks, factual-support results, and estimated API costs.

## Workflow

1. **Analyze the job:** Extract responsibilities, requirements, and relevant keywords into structured data.
2. **Select content:** Rank experience, projects, bullets, and skills using relevance scores and configured preferences.
3. **Tailor wording:** Rewrite selected bullets while preserving the claims in the source resume.
4. **Validate claims:** Compare candidate bullets with their source text and approved variants; restore source wording when a rewrite introduces unsupported claims.
5. **Render and fit:** Generate LaTeX, compile a PDF, and iteratively adjust content based on page count and layout constraints.
6. **Report results:** Record keyword coverage, possible duplicate bullets, layout warnings, token usage, and estimated cost.

Factual validation checks consistency with the supplied resume. It does not independently verify employment history or achievements. Keyword coverage measures text matches rather than predicting an employer's ATS ranking.

## Engineering focus

- Multi-stage LLM workflows with structured JSON responses.
- Python-enforced content, length, and layout constraints alongside model-based decisions.
- Concurrent job processing with `asyncio` and bounded API concurrency.
- Separate experience profiles and configurable project priorities.
- PDF compilation feedback for content selection and page fitting.
- Regression tests for selection rules, layout constraints, and PDF symbol rendering.

## Technology

Python, the OpenAI API, Pydantic, Jinja2, LaTeX, MiKTeX/pdflatex, and pypdf.

## Code guide

| Files | Responsibility |
| --- | --- |
| `pipeline.py` | Orchestrates analysis, rewriting, validation, rendering, and page fitting |
| `jd_analyzer.py`, `matcher.py` | Analyze job descriptions and score resume content |
| `rewriter.py`, `validator.py` | Tailor bullets and check factual support and local quality |
| `experience_profiles.py`, `project_priority.py`, `section_balance.py` | Apply personalized content-selection policies |
| `renderer.py`, `template.tex`, `compiler.py` | Produce LaTeX and compile PDFs |
| `resume_models.py`, `schemas.py` | Define data models and structured response schemas |
| `openai_service.py`, `report.py` | Manage API calls, retries, usage tracking, and reports |
| `tests/` | Check selection, rendering, and layout behavior |

## Scope of this public repository

Personal resume data, job-description folders, generated resumes, model caches, credentials, and private documentation are excluded through `.gitignore`. `resume_data.example.json` illustrates the data structure with placeholder content.

The example data is not a complete replacement for the private configuration. Some tests also depend on private resume data. This repository documents a personal project and is not maintained as a supported public product; setup instructions and a ready-to-run public deployment are intentionally outside its scope.
