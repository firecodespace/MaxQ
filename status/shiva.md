# Status - Shiva

Lane: `ascent/`, `manifest/`. Default WPs: 05, 06, 07, 10, 11 (+ Shiva half of WP-00).

## Waiting on / needed from Shiva (WP-00 Shiva half)
- `data/dummy/rocket_dummy.json`, `trajectory_dummy.json`, `manifest_instance_dummy.json`, `result_dummy.json`
  - must validate: `python -m contracts.validate data/dummy/<file>`
  - label as dummy in a top-level `_note` field (schemas allow it)
  - note: `result` needs `schema_version: 1` (HANDOFF 5.6 example omits it)
  - trajectory ids used by `risk_dummy.json`: `traj_medlift_a_w0`, `traj_medlift_a_w2`, `traj_medlift_a_w4` (windows w0, w2, w4 in `windows_dummy.json`)
- Your environment: see `requirements.txt` (pinned) and `make setup`.
- When your four dummies land, delete the `xfail` marker on `test_every_contract_has_a_dummy` in `tests/contracts/test_contracts.py`.

## Log
- 01:25 IST — WP-00 (Shiva half) DONE:
  - Landed `data/dummy/{rocket,trajectory,manifest_instance,result}_dummy.json`. All 4 validate against `contracts/validate.py`.
  - Removed `xfail` on `test_every_contract_has_a_dummy`; test suite now passes (53 passed, 1 skipped).
  - Pinned Shiva lane dependencies in `requirements.txt`: cma==4.5.0, pymoo==0.6.2, ortools==9.15.6755, pennylane==0.45.1, pennylane-lightning==0.45.0, matplotlib==3.11.2 (Yellow item noted). PennyLane installed cleanly on Python 3.13.
  - Trajectory dummy provides 521-point 1 Hz profile for `traj_medlift_a_w0` ready for Yash's frontend (WP-03) and screening (WP-08).
- 01:55 IST — WP-06 (Pitch/Staging Optimizer P1) DONE:
  - Shipped `ascent/optimize.py` implementing CMA-ES optimization of ascent pitch program parameters.
  - Achieved measured 626.3 kg propellant savings (0.13% total wet mass reduction, ~7% payload capacity equivalent) versus baseline, achieving 176.1 x 182.2 km parking orbit (e = 0.00047, max-q 42.4 kPa at 71.7 s).
  - Generated comparison artifact `data/results/naive_vs_optimized_medlift_a.png` (4-panel altitude, speed, dynamic pressure, pitch angle curves).
  - Exported contract-valid `data/results/trajectory_optimized_medlift_a.json` with seed=42 recorded in outputs.
  - Test suite `tests/ascent/test_optimize.py` added; full repo test suite passes (159 tests green).




## Note from Yash's agent (claude-code) - 02:10 IST: real windows are available
- `data/results/windows_crew_iss_cape_canaveral.json` (and cargo_iss / meo_gps / geo_comm variants): 7 days of REAL plane-crossing windows from the live ISS TLE. Use the `flyable: true` ascending ones (`w0`, `w2`, ...). Regenerate: `python -m mission.windows --profile all`.
- `azimuth_deg` is the INERTIAL azimuth of the target plane (44.9 deg Cape/ISS), not the heading relative to the rotating ground. For the ground-relative heading use `mission.phasing.rotating_azimuth_deg(lat, incl, v_orbit)` (~42.7 deg for the same case), or fly the inertial azimuth in an inertial-frame sim and add Earth rotation yourself.
- ISS inclination/altitude: read from the catalog TLE (`catalog.fetch.load_catalog()` after `python -m catalog.fetch --offline`), never hard-code. `core/frames.py` has geodetic/ECEF/TEME helpers (read-only for you).
- `geo_comm` windows are a labelled PLACEHOLDER (nominal daily slot).

## Finding from Yash's agent (claude-code) - 03:40 IST: trajectory frame/orbit inconsistency (WP-05/06 export)
- Evidence (collision screening, `catalog/screen.py`): treating `lat_deg/lon_deg/alt_m` as Earth-fixed WGS-84 geodetic (as the contract says), the orbit implied by the last positions of `trajectory_cargo_iss_w0.json` is ~192 x 1090 km, i = 50.5 deg, but `final_orbit` says 199.6 x 204.5 km, i = 51.6. For `trajectory_optimized_medlift_a.json` the positions imply a negative perigee while `final_orbit` says 176 x 182 km.
- `speed_mps` is consistent with the ground track (7735 m/s vs 7733 m/s from finite differences), so the sim is self-consistent; the mismatch looks like a frame/shape convention: `ascent/geo.py` maps downrange on a SPHERE (R = 6378.137 km, great-circle from the launch point), and the track seems to be an INERTIAL ground track (Earth rotation not subtracted from longitude). Expected size: ~omega*t*R*cos(lat) = ~185 km in longitude at insertion (t = 453 s) plus up to ~8 km radial at 38 deg latitude (sphere vs WGS-84).
- What I need from you (your lane; I did not touch `ascent/`): please confirm, and then either export Earth-fixed longitude (`lon_inertial - omega*t`) and WGS-84 geodetic altitude, or tell me the convention so the contract/consumers can state it. Until then, ascent-only screening uses the positions as the contract specifies, and `--coast-hours` refuses to run on these files.
- Quick check you can run: `python -m catalog.screen --windows data/results/windows_cargo_iss_cape_canaveral.json --trajectory data/results/trajectory_cargo_iss_w0.json --coast-hours 2` (prints the refusal with the implied orbit).
