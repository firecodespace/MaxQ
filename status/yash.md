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

## 02:10 IST - claude-code (on behalf of yash) - WP-02 launch windows and phasing: done
- Done: `mission/{sites,phasing,profiles,windows}.py`; SGP4 plane-crossing solver (60 s scan + brentq). Cape ISS: 2 crossings/day, ascending azimuth 44.87-44.89 deg, windows ~22 min earlier per day. Real output: `data/results/windows_{crew_iss,cargo_iss,meo_gps,geo_comm}_cape_canaveral.json` (contract-valid, committed). `python -m pytest` -> 106 passed, 1 skipped.
- Tests: all HANDOFF section 7 WP-02 known values + independent closed-form J2 cross-check of crossing times (agree < 90 s); descending passes flyable=false by default; stale-TLE and unreachable-inclination fail loudly.
- Caveats: azimuth is INERTIAL (see decisions); phasing assumes 520 s / 17 deg ascent; geo_comm is a placeholder. Launch Library 2 informational comparison not done (not pass/fail).
- Next: WP-03 frontend v0 (works on dummy data first). Resume: `.venv/Scripts/python.exe -m pytest`; `make windows` equivalent: `python -m mission.windows --profile all`.

## 02:50 IST - claude-code (on behalf of yash) - WP-03 frontend v0: done (local preview verified)
- Done: `web/` (index.html, css, js modules, worker, vendored satellite.js 5.0.0 + land data), `scripts/export_web_data.py` (+ tests). Map with 6,815 live objects (SGP4 in a Web Worker), group toggles, ISS track, day/night, click-to-inspect, zoom/pan; windows panel (4 profiles, click a row to jump the clock to the launch time, DUMMY/PLACEHOLDER/REAL badges); replay (play/scrub, event markers, alt/speed/q plots, follow vehicle, clock sync).
- Verified in the in-app browser at 1200x900 and 375x812: no console errors on a normal load, no horizontal overflow at 375; with land/catalog/trajectory files removed the page shows banners and keeps working. `python -m pytest` all green.
- Run: `python scripts/export_web_data.py && python -m http.server 8765 --directory web` (or `make web`).
- Real trajectory: Shiva's `trajectory_cargo_iss_w0.json` (WP-05, now on main) is exported and shown first in the replay selector (badge "SIM - reduced fidelity", no DUMMY banner); verified in the browser. The dummy trajectory is still selectable and bannered.
- Next: WP-04 deploy (clean-profile + mobile check, URL into this file). Deploy settings are RED if they touch other people's accounts/domains: need Yash's decision on host (Vercel vs GitHub Pages).
