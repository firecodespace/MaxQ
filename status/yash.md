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

## 03:05 IST - claude-code (on behalf of yash) - WP-04 deploy v1: LIVE
- **URL: https://maxq-nu.vercel.app** (alias also https://maxq-firecodespaces.vercel.app). Vercel project `firecodespaces/maxq`, hobby plan, account `firecodespace`; static, no build (`vercel.json` -> `web/`).
- Verified on the live URL in the in-app browser (fresh storage, no prior state): desktop 1200x900 and mobile 375x812, no console errors, no horizontal overflow, worker + 6,815 satellites + 14 windows + real trajectory replay all load; all assets 200 with correct content types, nosniff and cache headers present, public without login.
- Not verified: the user's own Chrome profile/other real devices; deploy was via CLI, so pushes to `main` do NOT auto-deploy (no Git integration). Re-deploy: `python scripts/export_web_data.py` then `npx vercel@65.14.0 deploy --prod --yes` from the repo root (needs `vercel login`; `.vercel/` link state is git-ignored).
- Contracts/shared files: only `vercel.json`, `.vercelignore` added.

## 03:40 IST - claude-code (on behalf of yash) - WP-08 collision screening: done (ascent-only; coast gated)
- Done: `catalog/screen.py` (prefilter, chunked SGP4 SatrecArray, coarse/1 Hz/0.1 s refinement, risk.json builder, `--coast-hours`, `--benchmark`), 14 tests; real outputs `data/results/risk_cargo_iss_cape_canaveral{,_optimized}.json` (7 flyable windows each, all `go`, closest object 318-2,249 km: the ascent stays below 200 km); exported to web/public/data. `python -m pytest` -> 177 passed.
- Budget: 30,000 objects x 2 h at 10 s unfiltered = 21.6 M samples in 7.7 s (2.8 M samples/s) vs ~60 s budget. Accuracy: engine matches a dense 0.1 s brute force within 0.25 km on injected conjunctions (3/15/40 km flagged no-go/review/go).
- Needs/decisions: (1) Shiva's trajectory frame/orbit inconsistency (see status/shiva.md) blocks coast screening; (2) the TLE-only catalog misses 6-digit objects, so results lean to `go` - revisit the OMM extension; (3) results are stated as illustrative in `_note`/metadata.
- Next: WP-09 clustering/congestion (P1) or WP-12 profiles/README. Resume: `make risk` (or the two `python -m catalog.screen ...` lines in the Makefile).
