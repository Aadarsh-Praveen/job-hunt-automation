# Resume Directory

- `base_resume.tex` — your master LaTeX template (copy from your existing file)
- `templates/` — reusable LaTeX fragments
- `tailored/` — output directory for per-job tailored resumes (gitignored)

## Compile manually

```bash
pdflatex -output-directory=tailored base_resume.tex
```

## Compile programmatically

The tailoring engine calls `pdflatex` via `src/resume/compile.py` and writes the resulting PDF to `tailored/{company}_{role}_{date}.pdf`.

## Important

`base_resume.tex` is the single source of truth for your experience. Tailoring only re-emphasizes, re-words, and re-orders. It NEVER invents experience you don't have.
