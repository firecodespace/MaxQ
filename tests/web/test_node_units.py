"""Run the JavaScript unit tests (tests/web/*.test.mjs) with Node's built-in test runner. Owner: Yash.

Skipped (not failed) when Node is not installed, so the Python suite still runs everywhere.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_unit_tests_pass():
    files = sorted(str(p) for p in (ROOT / "tests" / "web").glob("*.test.mjs"))
    assert files, "no *.test.mjs files found"
    proc = subprocess.run(
        [NODE, "--test", *files],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-1500:]
    assert "# fail 0" in proc.stdout
