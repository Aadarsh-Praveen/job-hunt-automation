"""Compile + score + diff a tailored resume.

WORKFLOW:
1. Open Claude.ai chat with this project (or use this conversation)
2. Pick a job from your Notion Applications DB — copy its JD link or text
3. Ask Claude: "Tailor my resume for this JD: <URL or pasted text>"
4. Claude reads base_resume.tex from project files, generates a tailored .tex
5. Save the tailored .tex locally as:
       resume/tailored/<company>_<role_slug>.tex
   Save the JD text alongside it:
       resume/tailored/<company>_<role_slug>.jd.txt
6. Run this script:
       uv run python scripts/tailor_resume.py \
           --tex  resume/tailored/<company>_<role_slug>.tex \
           --jd   resume/tailored/<company>_<role_slug>.jd.txt

What this produces:
    resume/tailored/<company>_<role_slug>.pdf   — compiled PDF, ready to submit
    resume/tailored/<company>_<role_slug>.diff.md — diff vs base_resume.tex
    Console output: ATS score breakdown, with matched/missing skills + phrases
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.common.logger import get_logger
from src.config import PROJECT_ROOT
from src.resume.compile import compile_tex
from src.resume.diff import write_diff_markdown
from src.resume.scorer import score_resume

logger = get_logger("tailor_resume")


def main(tex_path: Path, jd_path: Path) -> None:
    tex_path = Path(tex_path).resolve()
    jd_path = Path(jd_path).resolve()
    base_tex = PROJECT_ROOT / "resume" / "base_resume.tex"

    if not tex_path.exists():
        raise SystemExit(f"Tailored TeX not found: {tex_path}")
    if not jd_path.exists():
        raise SystemExit(f"JD file not found: {jd_path}")
    if not base_tex.exists():
        raise SystemExit(f"Base resume not found: {base_tex}")

    # --- 1. Compile tailored .tex to PDF ---
    logger.info("compiling_pdf", tex=str(tex_path))
    pdf_path = compile_tex(tex_path)
    print(f"\n  ✓ PDF compiled:  {pdf_path}")

    # --- 2. Score against JD ---
    resume_text = tex_path.read_text(encoding="utf-8")
    jd_text = jd_path.read_text(encoding="utf-8")
    score = score_resume(resume_text, jd_text)
    print(score)

    # --- 3. Show missing skills + phrases (these are tailoring targets) ---
    if score.missing_skills:
        print(f"  Missing skills the JD wants (consider re-emphasizing if you have them):")
        for s in score.missing_skills[:15]:
            print(f"    - {s}")
        print()

    if score.missing_phrases:
        print(f"  Missing phrases (top 10):")
        for p in score.missing_phrases[:10]:
            print(f"    - {p}")
        print()

    # --- 4. Generate diff vs base resume ---
    diff_path = tex_path.with_suffix(".diff.md")
    write_diff_markdown(base_tex, tex_path, diff_path)
    print(f"  ✓ Diff written:  {diff_path}")

    # --- 5. Verdict ---
    if score.composite >= 95:
        print(f"\n  🎯 At target. Ready to submit.\n")
    elif score.composite >= 80:
        print(
            f"\n  ⚠  Below 95% target ({score.composite:.1f}). "
            f"Either iterate in Claude.ai with missing skills/phrases above, "
            f"or accept this honest ceiling.\n"
        )
    else:
        print(
            f"\n  ❌ Score is low ({score.composite:.1f}). "
            f"This JD likely needs experience you don't have. Consider skipping.\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tex", required=True, help="Path to tailored .tex")
    parser.add_argument("--jd", required=True, help="Path to JD text file")
    args = parser.parse_args()
    main(Path(args.tex), Path(args.jd))