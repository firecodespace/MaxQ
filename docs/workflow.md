# Dev workflow notes (shared hygiene)

Why this exists: the same three frictions kept recurring (stale venv, slow `make test`, rebase conflicts on shared files).

## Environment
- `make doctor` (or `python scripts/check_env.py`) compares installed packages to the pins in `requirements.txt` and prints the fix.
  `make test` runs it first, and pytest prints a one-line environment status in its header.
- After pulling a change to `requirements.txt`: `make setup`.

## Tests: two tiers
- `make test` = fast tier (`-m "not slow"`, ~17 s). Run it before every merge to `main`.
- `make test-all` = everything (~100 s). Run it at checkpoints (03:00 sync, 04:30 go/no-go, 06:30 freeze) and before the final push.
- A test is "slow" if it takes more than ~4 s: add `@pytest.mark.slow` in its own file, or list its node id in `tests/conftest.py` (`SLOW`).

## Shared and generated files (what collides, and how it is handled)
- Append-only logs merge automatically (`.gitattributes` `merge=union`): `docs/decisions.md`, `status/yash.md`, `status/shiva.md`, `.gitignore`.
  Append at the end; do not edit other people's lines. `status/BOARD.md` is Yash's table: only Yash edits it.
- Generated outputs are deterministic (no timestamps/runtimes inside `data/results/risk_*.json` or `web/public/data/manifest.json`), and only
  their owner regenerates them: Yash - `windows_*`, `risk_*`, `web/public/data/*`; Shiva - `trajectory_*`, `rocket_search_*`, optimizer results.
  Do not hand-edit a generated file; change the generator and re-run it.
- `git fetch && git rebase origin/main` before every push; if a generated file conflicts, re-generate it, do not merge it by hand.
