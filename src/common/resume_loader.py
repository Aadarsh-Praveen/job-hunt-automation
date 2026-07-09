"""Load and strip resume/base_resume.tex for use as LLM scorer input.

Deliberately self-contained (own regex, no import from src.resume.scorer)
— that module pulls in scikit-learn, which lives in the optional `resume`
extras group. The scoring cron only runs `uv sync` (no --all-extras), so
importing scorer.py here would break it with a missing-dependency crash.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESUME_PATH = PROJECT_ROOT / "resume" / "base_resume.tex"


def _strip_latex(text: str) -> str:
    # Discard the preamble (packages, spacing/command setup) — it's pure
    # LaTeX plumbing with zero resume content, and its multi-arg commands
    # (\titlespacing{...}{...}{...}{...} etc.) don't fully match the
    # generic single-arg stripper below, leaking raw dimension tokens
    # like "1pt 0pt 0pt3pt2pt" into the output otherwise.
    body_match = re.search(r"\\begin\{document\}(.*)\\end\{document\}", text, flags=re.DOTALL)
    if body_match:
        text = body_match.group(1)

    # Strip common LaTeX cruft: commands, comments, math delimiters.
    # Comment-stripping must NOT eat escaped "\%" (a literal percent sign
    # in the content, e.g. "85\% accuracy") — only an unescaped "%" starts
    # a real comment. Getting this wrong truncates every percentage-based
    # achievement in the resume mid-sentence.
    text = re.sub(r"(?<!\\)%.*$", "", text, flags=re.MULTILINE)
    text = text.replace("\\%", "%")
    text = text.replace("\\&", "&")
    text = re.sub(r"\\(begin|end)\{[^}]+\}", "", text)
    text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)  # keep href label
    text = re.sub(r"\\textbf\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\textit\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\emph\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\section\*?\{([^}]*)\}", r"\n\n## \1\n", text)
    text = re.sub(r"\\subsection\*?\{([^}]*)\}", r"\n### \1\n", text)
    text = re.sub(r"\\item", "- ", text)
    text = re.sub(r"\\[a-zA-Z]+\*?\{?[^}]*\}?", "", text)  # generic strip
    text = re.sub(r"\\\\", "\n", text)
    text = re.sub(r"[\{\}]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@lru_cache(maxsize=1)
def load_resume_plaintext() -> str:
    """Return resume/base_resume.tex with LaTeX commands stripped.

    Cached — safe to call once per job in a batch without re-reading disk.
    Raises FileNotFoundError if the resume hasn't been placed yet (see
    resume/README.md).
    """
    if not RESUME_PATH.exists():
        raise FileNotFoundError(
            f"Base resume not found at {RESUME_PATH} — see resume/README.md "
            f"('copy from your existing file'). Required for the JD fit scorer."
        )

    return _strip_latex(RESUME_PATH.read_text(encoding="utf-8"))
