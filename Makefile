.PHONY: check format lint types test run migrate

PYTHON := .venv/bin/python

check: lint types test

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .

types:
	$(PYTHON) -m mypy main.py config.py database.py models.py scheduler.py bot/ modules/

test:
	$(PYTHON) -m pytest

run:
	$(PYTHON) -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

migrate:
	$(PYTHON) -m alembic upgrade head
