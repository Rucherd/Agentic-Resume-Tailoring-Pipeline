from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from config import ANALYSIS_MODEL, ANALYSIS_EFFORT, ROOT
from openai_service import service
from schemas import BOOTSTRAP_SCHEMA


def add_ids(raw: dict) -> dict:
    for i, edu in enumerate(raw.get("education", []), start=1):
        edu["id"] = f"edu{i}"

    for i, exp in enumerate(raw.get("experience", []), start=1):
        exp["id"] = f"exp{i}"
        exp["bullets"] = [
            {
                "id": f"exp{i}.b{j}",
                "text": text,
                "skills": [],
                "tags": [],
                "variants": {},
            }
            for j, text in enumerate(exp.get("bullets", []), start=1)
        ]

    for i, project in enumerate(raw.get("projects", []), start=1):
        project["id"] = f"proj{i}"
        project["bullets"] = [
            {
                "id": f"proj{i}.b{j}",
                "text": text,
                "skills": [],
                "tags": [],
                "variants": {},
            }
            for j, text in enumerate(project.get("bullets", []), start=1)
        ]

    return raw


async def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python bootstrap_resume.py master_resume.tex")
        raise SystemExit(2)

    source = Path(sys.argv[1])
    if not source.exists():
        raise FileNotFoundError(source)

    latex = source.read_text(encoding="utf-8")

    prompt = f"""
Extract ONLY factual resume information from the LaTeX resume below into the
provided JSON schema.

Rules:
- Do not invent or infer facts.
- Preserve dates, company names, titles, schools, project names, metrics,
  technologies, and bullet meaning exactly.
- Remove LaTeX formatting commands from textual content.
- Keep every substantive bullet.
- Skills must only include skills explicitly present in the resume.
- If a field is absent, return an empty string or empty list.
- This is a one-time migration step; accuracy is more important than style.

RESUME:
{latex}
"""

    raw, usage = await service.call_json(
        stage="bootstrap",
        model=ANALYSIS_MODEL,
        effort=ANALYSIS_EFFORT,
        prompt=prompt,
        schema_name="resume_bootstrap",
        schema=BOOTSTRAP_SCHEMA,
        max_output_tokens=12000,
    )

    raw = add_ids(raw)
    destination = ROOT / "resume_data.generated.json"
    destination.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote: {destination}")
    print("IMPORTANT: Review every fact manually, then rename it to resume_data.json.")
    if usage.estimated_cost_usd is not None:
        print(f"Estimated API cost: ${usage.estimated_cost_usd:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
