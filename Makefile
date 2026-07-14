.PHONY: up down test lint typecheck demo

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

# Filled in Milestone 8 (M8.T3): seeds all three profiles with known seeds,
# one guaranteed failure, one guaranteed cancellation.
demo:
	@echo "make demo: not implemented yet — see docs/todo.md Milestone 8 (M8.T3)"
	@exit 1
