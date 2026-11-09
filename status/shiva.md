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
- 02:20 IST — WP-07 (Rocket Design Search P1) DONE:
  - Shipped `ascent/design.py` implementing multi-objective launch vehicle sizing and staging optimization via NSGA-II (`pymoo`).
  - Objectives: Maximize payload capacity to target orbit, minimize liftoff wet mass (GLOW). Physical constraints: delta-V budget and stage acceleration limits.
  - Built adaptive pitch program tuning (`tune_pitch_program`) pairing coarse-grid heuristic search with Nelder-Mead polishing to verify candidate architectures in 2D point-mass ascent simulation.
  - Executed search across all 3 mission profiles (`cargo_iss`, `crew_iss`, `geo_comm`):
    - Produced non-dominated Pareto frontiers for each profile (saved to `data/results/pareto/pareto_designs_<profile>.json` and visual plots to `data/results/pareto_front_<profile>.png`).
    - Successfully verified all 9 finalists (Lightweight, Balanced, Heavyweight per profile) achieving circular parking orbits (perigee 175-198 km, e < 0.003, v >= 7760 m/s).
    - Exported 9 contract-valid rocket definitions (`data/results/rocket_search_<profile>_<label>.json`) validated 100% against `contracts/rocket.schema.json`.
  - Added unit test suite `tests/ascent/test_design.py` (7 tests green); full repository test suite passes (184 passed, 0 failed); `ruff check .` passes with 0 errors.





## Note from Yash's agent (claude-code) - 02:10 IST: real windows are available
- `data/results/windows_crew_iss_cape_canaveral.json` (and cargo_iss / meo_gps / geo_comm variants): 7 days of REAL plane-crossing windows from the live ISS TLE. Use the `flyable: true` ascending ones (`w0`, `w2`, ...). Regenerate: `python -m mission.windows --profile all`.
- `azimuth_deg` is the INERTIAL azimuth of the target plane (44.9 deg Cape/ISS), not the heading relative to the rotating ground. For the ground-relative heading use `mission.phasing.rotating_azimuth_deg(lat, incl, v_orbit)` (~42.7 deg for the same case), or fly the inertial azimuth in an inertial-frame sim and add Earth rotation yourself.
- ISS inclination/altitude: read from the catalog TLE (`catalog.fetch.load_catalog()` after `python -m catalog.fetch --offline`), never hard-code. `core/frames.py` has geodetic/ECEF/TEME helpers (read-only for you).
- `geo_comm` windows are a labelled PLACEHOLDER (nominal daily slot).
