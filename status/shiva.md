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

