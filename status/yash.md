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

## 04:05 IST - claude-code (on behalf of yash) - WP-08 follow-up: coast screening enabled after Shiva's frame fix
- Verified Shiva's fix (6418b01) on the real files: implied insertion orbits match `final_orbit` within a few km. `python -m catalog.screen ... --coast-hours 2` now works; `make risk` regenerated `data/results/risk_cargo_iss_cape_canaveral{,_optimized}.json` with ascent + 2 h parking-orbit coast (7 flyable windows, 0.6 s each): all `go`; closest object 27-315 km (baseline; w0 STARLINK-1274 at 26.98 km is 2 km above the 25 km threshold) and 42-474 km (optimized). Web data re-exported.
- Added regression test over every committed trajectory. `python -m pytest` -> 204 passed (~107 s, WP-07 design tests dominate).
- Caveat unchanged: TLE-only catalog misses 6-digit objects, so results lean to `go`; coast is a parking-orbit coast with no phasing maneuvers (stays near 200 km, so ISS-altitude objects are not approached).

## 04:40 IST - claude-code (on behalf of yash) - the three recurring caveats, fixed
1. Incomplete catalog -> FIXED: OMM JSON pipeline; 19,565 objects incl. 1,010 numbered >= 100000 (ISS visiting vehicles were among the missing). Contract extended additively; TLE renderer validated against CelesTrak's own text (4,423/4,424 exact). Completeness changed no window verdict today, but the screen can now say it covers the whole catalog. New real results: 2 `review` (baseline w14 10.2 km, optimized w0 7.1 km).
2. Slow suite -> FIXED: `make test` fast tier 19 s (was ~107 s); `make test-all` runs everything.
3. Rebase/venv -> FIXED: `make doctor` + pytest header flag stale venvs (my venv really was missing ortools/pennylane; now matches pins); union merges for shared logs; volatile fields removed from generated outputs; rules in docs/workflow.md.
- Not redeployed: the Vercel site still serves the previous export. Redeploy: `python scripts/export_web_data.py` then `npx vercel@65.14.0 deploy --prod --yes`.

## 05:10 IST - claude-code (on behalf of yash) - redeployed https://maxq-nu.vercel.app
- Production redeploy (CLI, deployment `maxq-mi93rxtez-firecodespaces.vercel.app`, READY) with the complete catalog (6,819 of 19,565 shown, 328 numbered 100000+), regenerated windows/risk and Shiva's WP-10 scheduler files exported to web/public/data.
- Verified on the live URL: assets 200; desktop 1200x900 and mobile 375x812 with no console errors and no horizontal overflow; replay scrubbing and clock lock work; no banners. The deployed content equals the repo's web/public/data at this commit.
- Reminder: deploys are manual (no Git integration): `python scripts/export_web_data.py` then `npx vercel@65.14.0 deploy --prod --yes`.

## 05:05 IST - claude-code (on behalf of yash) - WP-09 catalog clustering and congestion: done
- Done: `catalog/cluster.py` (`make cluster`), `data/results/clusters.json` (19,565 objects -> 24 clusters, 1.9 % noise, ~10 s), additive contract fields (embedding, metrics, congestion, per-cluster stats), Sky tab in the web UI (orbit map by cluster with legend/isolate/hover, congestion chart with density/count toggle and hotspot list), 22 tests. `python -m pytest -m "not slow"` -> 288 passed.
- Verified in the browser (desktop 1200x900, mobile 375x812): no console errors, no overflow, hover/legend/isolate/tab switching work.
- Result worth knowing: the most congested shell is 450-500 km (10,098 objects, Starlink); GEO belt, GPS-like MEO ring and the debris clouds separate cleanly; Starlink spans several shells so a single "starlink" label is not one cluster.
- Real ML piece: unsupervised DBSCAN (scikit-learn 1.9.1 pinned). Not redeployed: the Vercel site still has the previous export.
- Next: WP-12 (profiles UI, Risk + Manifest tabs, README, demo GIF), then redeploy.

## 04:20 IST - claude-code (on behalf of yash) - Shiva review done; board refreshed
- Reviewed WP-10/11/14: engineering OK, claim-level issues found (details `docs/reviews/shiva-wp10-11-14.md`, action list in `status/shiva.md`). Biggest: QAOA 100% is matched by random sampling with the same shots (90.6% mean, 24% optimal); CP-SAT beats SA on speed and quality; the 1000-task instance is 85% synthetic and unlabelled; reruns are not reproducible. Evidence script: `scripts/review_manifest_claims.py`.
- During the review I accidentally re-ran `manifest.bench` (it has no CLI and ignores --help); it overwrote Shiva's committed results; restored from git immediately, nothing was committed.
- My side: risk files carry `windows_profile`; BOARD brought up to date (WP-12 deferred to last stage by Yash's decision).

## 05:15 IST - claude-code (on behalf of yash) - WP-16 space-weather overlay: done
- Done: `web/js/spaceweather.js` (parse/validate NOAA rows, G scale, activity class, current/peak, cached fetch), header Kp chip, Sky-tab panel with chart, 9 Node unit tests wired into pytest. Verified in the browser with live NOAA data (Kp 3.7 quiet; a real G1 storm period on Nov 7-8 is visible), failure path ("Kp n/a" + message, recovers), mobile 375px (no overflow).
- Display only, labelled "Context only": no calculation uses it.
- Next: redeploy (Sky tab + WP-16), then re-check Shiva's fixes when they push; WP-12 last.

## 05:25 IST - claude-code (on behalf of yash) - redeployed https://maxq-nu.vercel.app (Sky tab + WP-16)
- Production deploy `maxq-kwlyeb0gi-firecodespaces.vercel.app` READY. Live now: complete catalog, Mission + Sky tabs (clusters, congestion), NOAA Kp chip and panel, regenerated windows/risk, Shiva's results exported.
- Verified on the live URL: assets 200; desktop 1200x900 and mobile 375x812 with no console errors and no horizontal overflow; Kp chip loads NOAA live from the deployed origin; Sky tab loads 24 clusters and the congestion chart.
- Still manual: `python scripts/export_web_data.py` then `npx vercel@65.14.0 deploy --prod --yes`. Not yet on the live site: nothing pending from my side; Shiva's review fixes will need another export+deploy.

## 05:50 IST - claude-code (on behalf of yash) - checked Shiva's push
- Code fixes verified (tests 293 pass, contracts 44/44, deterministic builder, argparse, control + generated paragraph, instance-level AUC). Committed artifacts are stale: instances differ from a fresh build, results/plots predate the fix, QAOA plot has no control. Asked Shiva to regenerate and recommit (status/shiva.md). Web data/redeploy and README numbers wait for that.

## 05:55 IST - claude-code (on behalf of yash) - checked Shiva's second push
- Manifest regeneration verified and accepted (details in docs/reviews, Round 2). Measured numbers recorded in docs/decisions.md for the README.
- WP-15 NOT accepted: the 'historical' ISS TLE sequence is synthetic (exact 12 h spacing, exact -2.5 deg RAAN/step, linear drift, identical ecc/incl) and undisclosed; the 86.9% reduction is recovery of a synthetic drift, tuned to a target. Do not cite it. Fix list for Shiva in status/shiva.md; real data would need a Space-Track account created by Yash.
- Also open: QAOA paragraph claims p=2 'recovers the optimum' (it scored 80.5%); tests rewrite a committed results file with a local absolute path.

## 06:20 IST - claude-code (on behalf of yash) - WP-15 fixed as far as possible without the account
- Done: synthetic data and invalid results removed; evaluation rebuilt for REAL histories only (refuses non-real input, clear error when the file is missing); fetch script written to Space-Track's rules; 22 new tests (all synthetic fixtures labelled, mocked network, nothing written to data/results).
- NEEDS YOU (credentials never touch me): 1) create a free account at https://www.space-track.org (approval can take from minutes to a couple of days); 2) in PowerShell from the repo folder: `$env:SPACETRACK_USER = "<email>"; $env:SPACETRACK_PASS = "<password>"; .venv\Scripts\python.exe scripts/fetch_spacetrack_history.py --norad 25544 --days 21` (ONE request; it refuses to run twice); 3) `.venv\Scripts\python.exe -m catalog.residuals`, then tell me; I will verify and commit the result whatever it shows (including 'no significant improvement').
- Until then WP-15 has no result and stays out of the README/resume.

## 06:45 IST - claude-code (on behalf of yash) - WP-15 DONE with real data
- You fetched the real history (70 element sets, 20.3 days, ISS) with the account and the fixed script; I verified it is real (irregular 0-31 h gaps, 68 distinct eccentricities, irregular RAAN steps, ends at the real catalog epoch), re-ran the evaluation independently (output identical), checked the files carry no credentials, and committed the data with its Space-Track citation plus the results and plot.
- Result: SGP4 error 0.7 km (<1 d) -> 20 km (5-7 d); held-out RMS reduction 19.7 % (90 % CI 16.2-23.7 %) for the age+phase ridge model; drift-only model -8.2 % (no help). See decisions.md 06:45 for the allowed wording and limits. `python -m pytest -m "not slow"` -> 315 passed.
