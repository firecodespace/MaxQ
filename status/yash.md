# Status - Yash (and Yash's agents)

Lane: `catalog/`, `mission/`, `web/`, `scripts/`, `api/`, `core/frames.py`. Default WPs: 00, 01, 02, 03, 04, 08, 09, 12.

## 01:25 IST - claude-code (on behalf of yash) - WP-00 Yash half
- Done: contracts + validator pushed (`contract: add schemas and validator`); tests `python -m pytest` -> 43 passed, 2 skipped, 1 xfailed (xfail = Shiva's dummies pending).
- Done: scaffold (dummy data for windows/risk/clusters/catalog, Makefile, pinned requirements, status files, decisions log, CLAUDE.md/GEMINI.md pointers).
- Trap: `make` is not installed on this machine (run the commands in the Makefile directly); git needs `safe.directory` for the worktree path.

## 01:55 IST - claude-code (on behalf of yash) - WP-01 catalog + frames: done
- Done: `catalog/fetch.py` (cached, 1 req/group/2h, no redirects/retries on errors), `catalog/propagate.py` (SGP4->TEME->geodetic), `core/{constants,frames}.py`, tests. `python -m pytest` -> 69 passed, 2 skipped, 1 xfailed. Live run: 18,555 objects (0 rejected). Round-trip geodetic->ECEF->geodetic max error 3e-8 m (limit 1 m). ISS at TLE epoch: alt 414.7 km, 7.666 km/s.
- Offline: `python -m catalog.fetch --offline` (raw cache, else committed `data/sample/celestrak_sample.json`, 72 real objects incl. ISS). Live: `python -m catalog.fetch`.
- DECIDED (Yash, 02:00): keep TLE-only. CelesTrak TLE format excludes catalog numbers >= 100000 (ran out 2026-07-11) -> newer objects are missing from the catalog (matters for WP-08). Option: OMM/JSON + optional columns (additive). See docs/decisions.md.
- Traps: `make` not installed here (run Makefile commands directly); git needs safe.directory for the worktree; CelesTrak `active` download took ~1 min.
- Next: WP-02 (mission/sites, windows, phasing, profiles). Resume: `.venv/Scripts/python.exe -m pytest`.
