from __future__ import annotations

from config import ANALYSIS_MODEL, ANALYSIS_EFFORT
from openai_service import service, UsageRecord
from schemas import JOB_ANALYSIS_SCHEMA


async def analyze_job(job_text: str, job_name: str) -> tuple[dict, UsageRecord]:
    prompt = f"""
You are analyzing a job description for a resume-tailoring pipeline.

Return a precise structured analysis.

Distinguish:
- actual minimum / must-have qualifications,
- preferred qualifications,
- responsibilities,
- ATS keywords that genuinely matter,
- hard requirements that could disqualify a candidate.

Do not add technologies or requirements that are not in the posting.
Do not over-weight generic words such as "communication" unless emphasized.

Choose layout_profile from the role's main responsibilities:
- firmware_embedded: firmware, embedded systems, RTOS, or microcontroller work.
- gpu_ai_compiler: GPU/CUDA programming, compiler development, or AI/ML work.
- software: other software roles, including web, backend, cloud, and data engineering.
For mixed roles, use the dominant hands-on work described in the posting.
An AI company, use of AI coding assistants, optional ML familiarity, or merely
using a C/C++ compiler does not make a role gpu_ai_compiler.

JOB IDENTIFIER:
{job_name}

JOB DESCRIPTION:
{job_text}
"""

    return await service.call_json(
        stage="jd_analysis",
        model=ANALYSIS_MODEL,
        effort=ANALYSIS_EFFORT,
        prompt=prompt,
        schema_name="job_analysis",
        schema=JOB_ANALYSIS_SCHEMA,
    )
