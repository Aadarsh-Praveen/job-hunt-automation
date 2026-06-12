"""Generate a diff between base_resume.tex and a tailored version."""

from __future__ import annotations

import difflib
from pathlib import Path


def generate_diff(base_tex: Path | str, tailored_tex: Path | str) -> str:
    """Return a unified diff as a string."""
    base_tex = Path(base_tex)
    tailored_tex = Path(tailored_tex)

    base_lines = base_tex.read_text(encoding="utf-8").splitlines(keepends=True)
    tail_lines = tailored_tex.read_text(encoding="utf-8").splitlines(keepends=True)

    diff_iter = difflib.unified_diff(
        base_lines,
        tail_lines,
        fromfile=base_tex.name,
        tofile=tailored_tex.name,
        n=2,
    )
    return "".join(diff_iter)


def write_diff_markdown(
    base_tex: Path | str,
    tailored_tex: Path | str,
    output_path: Path | str,
) -> Path:
    """Write the diff as a markdown file with a fenced ```diff block."""
    base_tex = Path(base_tex)
    tailored_tex = Path(tailored_tex)
    output_path = Path(output_path)

    diff_text = generate_diff(base_tex, tailored_tex)

    content = (
        f"# Resume Diff\n\n"
        f"**Base:** `{base_tex.name}`\n"
        f"**Tailored:** `{tailored_tex.name}`\n\n"
        f"---\n\n"
        f"```diff\n{diff_text}\n```\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path