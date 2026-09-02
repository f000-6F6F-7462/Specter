PY ?= .venv/bin/python
PIP ?= $(PY) -m pip

.PHONY: help venv install fmt lint type test cov contracts clean

help:
	@echo "venv       create .venv (Python 3.14)"
	@echo "install    install the package + dev extras (editable)"
	@echo "fmt        isort + black (write)"
	@echo "lint       isort + black (check) + pylint"
	@echo "type       mypy"
	@echo "test       pytest"
	@echo "cov        pytest under coverage + report"
	@echo "contracts  regenerate contracts/jsonschema/*.schema.json"

venv:
	python3.14 -m venv .venv
	$(PIP) install --upgrade pip

install:
	$(PIP) install -e ".[dev]"

fmt:
	$(PY) -m isort src tests
	$(PY) -m black src tests

lint:
	$(PY) -m isort --check-only src tests
	$(PY) -m black --check src tests
	$(PY) -m pylint src

type:
	$(PY) -m mypy

test:
	$(PY) -m pytest

cov:
	$(PY) -m coverage run -m pytest
	$(PY) -m coverage report

contracts:
	$(PY) -m specter.contracts.export contracts/jsonschema

clean:
	rm -rf .pytest_cache .mypy_cache .coverage htmlcov **/__pycache__
