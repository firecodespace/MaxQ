"""Check that the installed packages match requirements.txt (pins), with an actionable message.

    python scripts/check_env.py            # exit 1 if anything is missing or differs
    python scripts/check_env.py --quiet

Used by `make doctor`, `make test` and tests/conftest.py (report header), so a stale venv is explained in one line
instead of as a wall of ModuleNotFoundError at collection time.

Owner: Yash (lane: scripts/).
"""

from __future__ import annotations

import argparse
import re
import sys
from importlib import metadata
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"
PIN = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([^\s#]+)")


def read_pins(path: Path = REQUIREMENTS) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = PIN.match(line)
        if m:
            pins[m.group(1)] = m.group(2)
    return pins


def check(path: Path = REQUIREMENTS) -> tuple[list[str], list[str]]:
    """(missing, mismatched) package descriptions."""
    missing, mismatched = [], []
    for name, want in read_pins(path).items():
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(f"{name}=={want}")
            continue
        if have != want:
            mismatched.append(f"{name}: installed {have}, pinned {want}")
    return missing, mismatched


def summary(path: Path = REQUIREMENTS) -> str | None:
    """One-line problem summary, or None when the environment matches the pins."""
    missing, mismatched = check(path)
    if not missing and not mismatched:
        return None
    parts = []
    if missing:
        parts.append(f"{len(missing)} pinned package(s) not installed ({', '.join(m.split('==')[0] for m in missing)})")
    if mismatched:
        parts.append(f"{len(mismatched)} differ from the pin ({', '.join(m.split(':')[0] for m in mismatched)})")
    return "; ".join(parts) + ". Fix with: python -m pip install -r requirements.txt  (or: make setup)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    missing, mismatched = check()
    if not missing and not mismatched:
        if not args.quiet:
            print(f"environment OK: {len(read_pins())} pinned packages match requirements.txt "
                  f"(Python {sys.version_info.major}.{sys.version_info.minor})")
        return 0
    for m in missing:
        print(f"MISSING    {m}")
    for m in mismatched:
        print(f"MISMATCH   {m}")
    print("Fix with: python -m pip install -r requirements.txt   (or: make setup)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
