.PHONY: test lint typecheck check

test:
	python -m pytest

lint:
	python -m ruff check src tests

typecheck:
	python -m mypy src

check: test lint typecheck
