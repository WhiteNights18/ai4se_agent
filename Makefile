PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)

.PHONY: test lint typecheck quality binary check

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src tests

typecheck:
	$(PYTHON) -m mypy src

quality: lint typecheck

binary:
	PYTHON="$(PYTHON)" ./scripts/build_binary.sh

check: test quality
