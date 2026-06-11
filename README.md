# Job Hunt Automation

Automated discovery, tailoring, and outreach pipeline for AI/ML/Data roles.

## What this does

- Pulls new jobs from Greenhouse, Lever, Ashby, Workable, Recruitee, Personio, Workday, FAANG career portals, and Indeed (via official MCP) — twice daily.
- Tailors your LaTeX resume per JD (interactive in Claude.ai) targeting 95% ATS keyword match.
- Discovers recruiter and hiring-manager contacts via Apollo/Hunter free tiers with aggressive caching.
- Sends cold outreach via Gmail with a 3-week follow-up cadence.
- Tracks everything in Notion: applications, contacts, companies, follow-up queue.
- Backs up Notion to Google Drive weekly.

See [`plan.md`](./plan.md) for the full architecture.

## Setup

```bash
# 1. Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install dependencies
make install

# 3. Set up environment variables
cp .env.example .env
# Edit .env with your actual API keys

# 4. Add your base LaTeX resume
cp /path/to/your/base_resume.tex resume/base_resume.tex

# 5. Create the 4 Notion databases (one-time)
make seed

# 6. Run a test fetch
make fetch
```

## Daily workflow

- **Automated:** GitHub Actions runs fetcher at 7am + 7pm ET, follow-up digest Monday 8am, Notion backup Sunday 11pm.
- **Manual (you):** review new jobs in Notion each morning, ask Claude.ai to tailor resume for interesting ones, submit applications.

## Project structure

See [`plan.md`](./plan.md) Section 2 for the module breakdown.

## License

Private repo. Not for redistribution.
