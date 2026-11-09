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
(Shiva and Shiva's agents: append status/handoff notes below.)
