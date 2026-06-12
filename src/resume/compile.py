"""Compile .tex files to PDF using pdflatex (locally installed)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.common.logger import get_logger

logger = get_logger("resume.compile")


def compile_tex(
    tex_path: Path | str,
    output_dir: Path | str | None = None,
    keep_aux: bool = False,
) -> Path:
    """Compile a .tex file to PDF.

    Returns the path to the generated PDF.
    Raises FileNotFoundError if .tex doesn't exist.
    Raises RuntimeError if pdflatex fails or PDF not generated.
    """
    tex_path = Path(tex_path).resolve()
    if not tex_path.exists():
        raise FileNotFoundError(f"TeX file not found: {tex_path}")

    output_dir = Path(output_dir).resolve() if output_dir else tex_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Run pdflatex twice so any internal references resolve cleanly
    result = None
    for _ in range(2):
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-output-directory={output_dir}",
                str(tex_path),
            ],
            capture_output=True,
            text=True,
            cwd=output_dir,
        )

    if result is None or result.returncode != 0:
        excerpt = "\n".join(result.stdout.splitlines()[-30:]) if result else ""
        logger.error(
            "pdflatex_failed",
            return_code=result.returncode if result else -1,
            tex=str(tex_path),
        )
        raise RuntimeError(f"pdflatex failed:\n{excerpt}")

    pdf_path = output_dir / (tex_path.stem + ".pdf")
    if not pdf_path.exists():
        raise RuntimeError(f"PDF was not generated at {pdf_path}")

    # Clean up auxiliary files unless asked to keep
    if not keep_aux:
        for ext in (".aux", ".log", ".out", ".toc", ".fdb_latexmk", ".fls", ".synctex.gz"):
            aux = output_dir / (tex_path.stem + ext)
            if aux.exists():
                aux.unlink()

    logger.info("pdflatex_compiled", tex=str(tex_path), pdf=str(pdf_path))
    return pdf_path