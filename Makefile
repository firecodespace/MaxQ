# MaxQ Makefile. One command per stage. Targets for work that does not exist yet print
# "not implemented yet" and exit 0 so `make all`-style chains never break.
#
# Python: uses ./.venv if present (Windows or POSIX layout), else `python` on PATH.
# Override with:  make test PY=/path/to/python

PY ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,$(if $(wildcard .venv/bin/python),.venv/bin/python,python))

.PHONY: setup doctor test test-all lint data data-offline windows risk ascent manifest web web-export

setup:  ## create .venv (if missing) and install pinned requirements
	@test -d .venv || python -m venv .venv
	$(PY) -m pip install -r requirements.txt

doctor:  ## check installed packages against the pins in requirements.txt
	$(PY) scripts/check_env.py

test: doctor  ## fast tier: everything except tests marked slow (what you run before every merge)
	$(PY) -m pytest -m "not slow"

test-all:  ## full suite including slow tests (run before pushing to main at checkpoints / freeze)
	$(PY) -m pytest

lint:  ## ruff
	$(PY) -m ruff check .

data:  ## WP-01: fetch CelesTrak (cached, max 1 request/group/2h) -> data/processed/catalog.parquet
	$(PY) -m catalog.fetch

data-offline:  ## WP-01: same, but no network (raw cache, else committed data/sample)
	$(PY) -m catalog.fetch --offline

windows:  ## WP-02: 7 days of launch windows, all profiles, Cape Canaveral -> data/results/windows_*.json
	$(PY) -m mission.windows --profile all --site cape_canaveral --days 7

risk:  ## WP-08: screen ascent + 2 h parking-orbit coast against the catalog -> data/results/risk_*.json
	$(PY) -m catalog.screen --windows data/results/windows_cargo_iss_cape_canaveral.json --trajectory data/results/trajectory_cargo_iss_w0.json --coast-hours 2
	$(PY) -m catalog.screen --windows data/results/windows_cargo_iss_cape_canaveral.json --trajectory data/results/trajectory_optimized_medlift_a.json --coast-hours 2 --out data/results/risk_cargo_iss_cape_canaveral_optimized.json

ascent:  ## WP-05/06: ascent simulation -> trajectory.json (Shiva)
	@echo "ascent: not implemented yet (WP-05, Shiva's lane)"

manifest:  ## WP-10: manifest scheduler benchmark (Shiva)
	@echo "manifest: not implemented yet (WP-10, Shiva's lane)"

web-export:  ## WP-03: validate + export data for the static site -> web/public/data/
	$(PY) scripts/export_web_data.py

web: web-export  ## WP-03: export data, then serve web/ at http://localhost:8765
	$(PY) -m http.server 8765 --directory web
