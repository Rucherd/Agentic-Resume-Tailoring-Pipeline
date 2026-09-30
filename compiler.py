from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pypdf import PdfReader


def find_pdflatex() -> str | None:
    return shutil.which("pdflatex")


def compile_tex(tex_path: Path) -> tuple[Path | None, str]:
    pdflatex = find_pdflatex()
    if not pdflatex:
        return None, (
            "pdflatex was not found. Install MiKTeX, then reopen PowerShell "
            "and verify: where.exe pdflatex"
        )

    out_dir = tex_path.parent
    cmd = [
        pdflatex,
        "-interaction=nonstopmode",
        "-halt-on-error",
        f"-output-directory={out_dir}",
        str(tex_path),
    ]

    proc = subprocess.run(
        cmd,
        cwd=out_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    pdf_path = tex_path.with_suffix(".pdf")
    if proc.returncode != 0 or not pdf_path.exists():
        tail = (proc.stdout + "\n" + proc.stderr)[-5000:]
        return None, tail

    return pdf_path, ""


def page_count(pdf_path: Path) -> int:
    return len(PdfReader(str(pdf_path)).pages)


def cleanup_aux(directory: Path) -> None:
    for ext in ("*.aux", "*.log", "*.out"):
        for path in directory.glob(ext):
            try:
                path.unlink()
            except OSError:
                pass
