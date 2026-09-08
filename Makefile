PY ?= .venv/bin/python
PIP ?= $(PY) -m pip

.PHONY: help venv install fmt lint type test cov contracts migrate revision run-api run-enroll run-ingest clean

help:
	@echo "venv       create .venv (Python 3.14)"
	@echo "install    install the package + dev extras (editable)"
	@echo "fmt        isort + black (write)"
	@echo "lint       isort + black (check) + pylint"
	@echo "type       mypy"
	@echo "test       pytest"
	@echo "cov        pytest under coverage + report"
	@echo "contracts  regenerate contracts/jsonschema/*.schema.json"
	@echo "migrate    alembic upgrade head"
	@echo "revision   alembic revision --autogenerate -m \"$$m\""
	@echo "run-api    uvicorn dev server on :8000"
	@echo "run-enroll  the specter-enroll worker (needs Redis)"
	@echo "run-ingest  the specter-ingest pipeline supervisor"

venv:
	python3.14 -m venv .venv
	$(PIP) install --upgrade pip

install:
	$(PIP) install -e ".[dev]"

fmt:
	$(PY) -m isort src tests alembic
	$(PY) -m black src tests alembic

lint:
	$(PY) -m isort --check-only src tests alembic
	$(PY) -m black --check src tests alembic
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

migrate:
	$(PY) -m alembic upgrade head

revision:
	$(PY) -m alembic revision --autogenerate -m "$(m)"

run-api:
	$(PY) -m uvicorn specter.entrypoints.http.asgi:create_app --factory --reload --port 8000

run-enroll:
	$(PY) -m specter.entrypoints.workers.enroll_worker

run-ingest:
	$(PY) -m specter.entrypoints.workers.ingest_worker

clean:
	rm -rf .pytest_cache .mypy_cache .coverage htmlcov **/__pycache__
