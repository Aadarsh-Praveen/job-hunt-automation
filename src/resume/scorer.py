"""ATS scorer: composite of TF-IDF cosine, skill coverage, and exact phrase match.

Weights:
  - TF-IDF cosine:       40%   (overall lexical similarity, on LaTeX-stripped text)
  - Skill coverage:      35%   (how many JD tech skills appear in resume)
  - Exact phrase match:  25%   (how many JD multi-word phrases appear)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


TECH_VOCAB: set[str] = {
    # Languages
    "python", "javascript", "typescript", "java", "c++", "go", "golang", "rust",
    "scala", "ruby", "swift", "kotlin", "r", "sql", "bash",
    # Web / Frontend
    "react", "react.js", "next.js", "vue", "angular", "svelte", "tailwind",
    "redux", "node.js", "express", "html", "css",
    # Backend / API
    "fastapi", "flask", "django", "spring", "rails", "graphql", "rest", "grpc",
    "pydantic", "celery",
    # AI / ML / GenAI
    "tensorflow", "pytorch", "scikit-learn", "sklearn", "keras", "xgboost",
    "lightgbm", "pandas", "numpy", "polars", "transformers", "huggingface",
    "langchain", "llamaindex", "rag", "llm", "llms", "openai", "anthropic",
    "claude", "gpt", "gemini", "embeddings", "vector database", "vector databases",
    "pinecone", "faiss", "qdrant", "fine-tuning", "lora", "rlhf",
    "prompt engineering", "agentic", "multi-agent", "ragas", "ollama", "vllm",
    "react agent", "tool use", "orchestration", "agent workflows", "agentic workflows",
    # Data Engineering
    "spark", "kafka", "airflow", "dbt", "snowflake", "bigquery", "redshift",
    "postgresql", "postgres", "mysql", "mongodb", "redis", "cassandra",
    "etl", "elt", "data pipeline", "data warehouse",
    # Cloud / DevOps / MLOps
    "aws", "azure", "gcp", "google cloud", "ec2", "s3", "lambda", "sagemaker",
    "kubernetes", "docker", "terraform", "ansible", "ci/cd", "github actions",
    "jenkins", "mlflow", "weights and biases", "dvc", "opentelemetry",
    # Concepts
    "machine learning", "deep learning", "neural networks", "nlp",
    "computer vision", "reinforcement learning", "supervised learning",
    "unsupervised learning", "clustering", "classification", "regression",
    "feature engineering", "hyperparameter tuning", "a/b testing",
    "statistical modeling", "predictive analytics", "data science",
    "time series", "anomaly detection", "recommendation systems",
}


@dataclass
class ScoreBreakdown:
    """Detailed scoring output."""

    composite: float
    tfidf_cosine: float
    skill_coverage: float
    exact_phrase_ratio: float
    matched_skills: list[str]
    missing_skills: list[str]
    matched_phrases: list[str]
    missing_phrases: list[str]

    def __str__(self) -> str:
        return (
            f"\n{'='*60}\n"
            f"  ATS Score: {self.composite:.1f} / 100\n"
            f"{'='*60}\n"
            f"  TF-IDF cosine:    {self.tfidf_cosine:.3f}\n"
            f"  Skill coverage:   {self.skill_coverage:.1%}  "
            f"({len(self.matched_skills)}/{len(self.matched_skills) + len(self.missing_skills)})\n"
            f"  Phrase coverage:  {self.exact_phrase_ratio:.1%}  "
            f"({len(self.matched_phrases)}/{len(self.matched_phrases) + len(self.missing_phrases)})\n"
            f"{'='*60}\n"
        )


# ---------------------------------------------------------------------------
# LaTeX stripping — convert .tex to plain text before scoring.
# Without this, LaTeX commands dominate the TF-IDF vector and crush the score.
# ---------------------------------------------------------------------------

def strip_latex(text: str) -> str:
    """Strip LaTeX commands/structure, keeping plain content."""
    # LaTeX comments (preserve escaped \%)
    text = re.sub(r'(?<!\\)%.*', '', text)
    # \begin{...} / \end{...} environment markers
    text = re.sub(r'\\(?:begin|end)\s*\{[^}]+\}', ' ', text)
    # \href{url}{label}  — keep the label
    text = re.sub(r'\\href\s*\{[^}]*\}\s*\{([^}]*)\}', r'\1', text)
    # \textbf{x}, \textit{x}, etc. — keep the content
    text = re.sub(
        r'\\(?:textbf|textit|emph|underline|texttt|nolinkurl|textsc)\s*\{([^}]*)\}',
        r'\1', text,
    )
    # Generic single-arg commands — keep the argument
    text = re.sub(r'\\[a-zA-Z]+\*?\s*\{([^}]*)\}', r'\1', text)
    # Bare commands (no arg) — \item, \hfill, \LaTeX, \\, etc.
    text = re.sub(r'\\[a-zA-Z]+\*?', ' ', text)
    text = re.sub(r'\\\\', ' ', text)
    # Stray braces, math markers, percent signs
    text = re.sub(r'[{}$]', ' ', text)
    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip()


# ---------------------------------------------------------------------------
# Skill + phrase extraction
# ---------------------------------------------------------------------------

def extract_skills(text: str, vocab: set[str] = TECH_VOCAB) -> set[str]:
    """Find tech vocabulary terms present in the text (case-insensitive)."""
    text_lower = text.lower()
    found: set[str] = set()
    for term in vocab:
        if " " in term or "." in term or "/" in term or "+" in term or "-" in term:
            if term in text_lower:
                found.add(term)
        else:
            if re.search(rf"\b{re.escape(term)}\b", text_lower):
                found.add(term)
    return found


def extract_exact_phrases(
    jd_text: str,
    min_words: int = 2,
    max_words: int = 5,
) -> list[str]:
    """Extract capitalized multi-word phrases from JD, line-by-line.

    Line-by-line prevents capturing chunks across section breaks.
    Hyphens allowed inside tokens (so 'Customer-Facing' stays whole).
    """
    skip_fragments = (
        'required skills', 'preferred skills', 'must have', 'nice to have',
        'what you', 'about you', 'about us', 'benefits', 'compensation',
        'equal opportunity',
    )

    # [A-Z]-led tokens allowing letters, digits, +/.- inside
    token = r'[A-Z][A-Za-z0-9+/.\-]+'
    connectors = r'(?:of|and|for|in|the|to|on|at|with)'
    pattern = rf'\b({token}(?:\s+(?:{token}|{connectors})){{{min_words - 1},{max_words - 1}}})\b'

    phrases: list[str] = []
    for line in jd_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        for match in re.finditer(pattern, line):
            phrase = match.group(1).strip()
            phrase_lower = phrase.lower()

            if any(s in phrase_lower for s in skip_fragments):
                continue

            words = phrase.split()
            capitalized = sum(1 for w in words if w and w[0].isupper())
            if capitalized < min_words:
                continue

            phrases.append(phrase)

    # Dedupe preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for p in phrases:
        if p.lower() not in seen:
            seen.add(p.lower())
            unique.append(p)
    return unique


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def score_resume(resume_text: str, jd_text: str) -> ScoreBreakdown:
    """Compute composite ATS score for a resume against a JD.

    Resume can be raw LaTeX — we strip it internally before vectorizing.
    """
    resume_clean = strip_latex(resume_text)

    # --- TF-IDF cosine similarity ---
    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        max_features=5000,
        lowercase=True,
    )
    try:
        vecs = vectorizer.fit_transform([resume_clean, jd_text])
        cos_sim = float(cosine_similarity(vecs[0], vecs[1])[0][0])
    except Exception:
        cos_sim = 0.0

    # --- Skill coverage ---
    jd_skills = extract_skills(jd_text)
    resume_skills = extract_skills(resume_clean)
    matched_skills = sorted(jd_skills & resume_skills)
    missing_skills = sorted(jd_skills - resume_skills)
    skill_cov = (len(matched_skills) / len(jd_skills)) if jd_skills else 1.0

    # --- Exact phrase coverage (against LaTeX-stripped resume) ---
    jd_phrases = extract_exact_phrases(jd_text)
    resume_lower = resume_clean.lower()
    matched_phrases = [p for p in jd_phrases if p.lower() in resume_lower]
    missing_phrases = [p for p in jd_phrases if p.lower() not in resume_lower]
    phrase_ratio = (len(matched_phrases) / len(jd_phrases)) if jd_phrases else 1.0

    composite = (cos_sim * 0.40 + skill_cov * 0.35 + phrase_ratio * 0.25) * 100

    return ScoreBreakdown(
        composite=round(composite, 2),
        tfidf_cosine=round(cos_sim, 4),
        skill_coverage=round(skill_cov, 4),
        exact_phrase_ratio=round(phrase_ratio, 4),
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        matched_phrases=matched_phrases,
        missing_phrases=missing_phrases,
    )