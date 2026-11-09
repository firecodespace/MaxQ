# MaxQ Makefile. One command per stage. Targets for work that does not exist yet print
# "not implemented yet" and exit 0 so `make all`-style chains never break.
#
# Python: uses ./.venv if present (Windows or POSIX layout), else `python` on PATH.
# Override with:  make test PY=/path/to/python

PY ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,$(if $(wildcard .venv/bin/python),.venv/bin/python,python))

.PHONY: setup test lint data data-offline windows ascent manifest web

setup:  ## create .venv (if missing) and install pinned requirements
	@test -d .venv || python -m venv .venv
	$(PY) -m pip install -r requirements.txt

test:  ## run all tests (contracts, catalog, frames, ...)
	$(PY) -m pytest

lint:  ## ruff
	$(PY) -m ruff check .

data:  ## WP-01: fetch CelesTrak (cached, max 1 request/group/2h) -> data/processed/catalog.parquet
	$(PY) -m catalog.fetch

data-offline:  ## WP-01: same, but no network (raw cache, else committed data/sample)
	$(PY) -m catalog.fetch --offline

windows:  ## WP-02: launch windows -> windows.json
	@echo "windows: not implemented yet (WP-02)"

ascent:  ## WP-05/06: ascent simulation -> trajectory.json (Shiva)
	@echo "ascent: not implemented yet (WP-05, Shiva's lane)"

manifest:  ## WP-10: manifest scheduler benchmark (Shiva)
	@echo "manifest: not implemented yet (WP-10, Shiva's lane)"

web:  ## WP-03: build/serve the static site
	@echo "web: not implemented yet (WP-03)"
