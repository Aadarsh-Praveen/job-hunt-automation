.PHONY: help install fetch tailor followup backup test lint format seed

help:
	@echo "Available commands:"
	@echo "  make install   - Install dependencies via uv"
	@echo "  make fetch     - Run the job fetcher (writes new jobs to Notion)"
	@echo "  make tailor    - Tailor resume for a specific JD (CLI prompt)"
	@echo "  make followup  - Send Monday follow-up digest email"
	@echo "  make backup    - Backup Notion DBs to Google Drive"
	@echo "  make seed      - One-time: create the 4 Notion databases"
	@echo "  make test      - Run pytest"
	@echo "  make lint      - Run ruff lint"
	@echo "  make format    - Run ruff format"

install:
	uv sync --all-extras

fetch:
	uv run python scripts/fetch_jobs.py

tailor:
	uv run python scripts/tailor_resume.py

followup:
	uv run python scripts/send_followups.py

backup:
	uv run python scripts/backup_notion.py

seed:
	uv run python scripts/seed_notion.py

test:
	uv run pytest -v

lint:
	uv run ruff check src/ scripts/ tests/

format:
	uv run ruff format src/ scripts/ tests/
