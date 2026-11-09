"""Shared pytest setup (owner: Yash).

* Test tiers: tests marked ``slow`` (> ~4 s each) are skipped by ``make test`` (``-m "not slow"``) and run by
  ``make test-all``. Mark a test with ``@pytest.mark.slow`` in its own file, or list its node id suffix in
  ``SLOW`` below (used for the WP-05/06/07 tests so their owner's files stay untouched).
* A one-line environment report at the top of the run when installed packages differ from requirements.txt.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# Measured 2026-11-10 (whole ascent suite = 82 s; everything else in the repo < 20 s together).
SLOW = (
    "tests/ascent/test_design.py::test_search_determinism",
    "tests/ascent/test_design.py::test_search_rocket_designs_execution_and_verification",
    "tests/ascent/test_design.py::test_tune_pitch_program_inserts_into_orbit",
    "tests/ascent/test_optimize.py::test_optimizer_improves_baseline",
    "tests/ascent/test_optimize.py::test_deterministic_reproducibility",
)


def pytest_report_header(config):
    try:
        from check_env import summary

        problem = summary()
    except Exception as e:  # noqa: BLE001 - never break test collection over a diagnostics helper
        return f"environment check unavailable: {e}"
    return f"environment: {problem}" if problem else "environment: matches requirements.txt pins"


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.nodeid.replace("\\", "/") in SLOW and "slow" not in item.keywords:
            item.add_marker(pytest.mark.slow)
