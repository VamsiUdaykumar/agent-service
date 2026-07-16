.PHONY: up down test lint typecheck demo openapi

up:
	docker compose up --build

down:
	docker compose down

test:
	SIM_SPEED=100 pytest

lint:
	ruff check .

typecheck:
	mypy app

# Seeds all three profiles with known seeds, one guaranteed failure, one
# guaranteed cancellation (M8.T3). Assumes `docker compose up` is already
# running — the two-command startup promise (PRD §6).
demo:
	python scripts/demo.py

# Regenerates the committed openapi.json (M9.T1.2) after a route/schema change.
openapi:
	python scripts/export_openapi.py
