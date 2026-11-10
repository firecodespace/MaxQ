# Yash - status

## 2026-11-11 - Phase 2 kickoff (WP2-00, WP2-01)

**Done**
- WP2-00: eight Phase 2 contracts + dummy data + validator rules + tests. `make contracts` validates
  everything; `python -m pytest tests/contracts -q` is green.
- WP2-01: `data/rockets/` (Falcon 9 Block 5, Atlas V 401, Antares 230+) and `data/pads/` (LC-39A,
  SLC-40, SLC-41, MARS LP-0A), every figure cited with its retrieval date. Rebuild with `make rockets`.
- Measured payload to LEO against published: antares_230plus: sim 10,600 kg vs published 8,000 kg (+32.5 %); atlas_v_401: sim 4,410 kg vs published 9,800 kg (-55.0 %); falcon9_block5: sim 26,790 kg vs published 22,800 kg (+17.5 %). The simulator over-delivers; causes are in
  docs/decisions.md and recorded per vehicle in `sim_validation`.

**Traps found (worth knowing before WP2-02)**
- Feasibility is not monotonic in payload: Atlas V 401 fails at 980 kg and succeeds at 4,900 kg,
  because spare delta-v goes into climbing. Do not bisect on payload.
- `ascent/optimize.py`'s pitch bounds are fitted to the fictional archetypes and are too narrow for
  real vehicles; they must be widened or the flight lofts and the perigee stays underground.
- Atlas V 401's Centaur has a thrust-to-weight of 0.42 and a 928 s burn in this model, so each
  simulation is slow. Expect minutes, not seconds, when it is in a search loop.

**Next**
- WP2-06/07/08 (API, wizard, mission control) once Shiva's WP2-02 MissionEnv and WP2-04 verifier land.
- The 3D view is being replaced wholesale, so WP2-09 is on hold by Yash's decision.

**Blocked / needs**
- Model and licence choice for the LLM planner is still Red and unmade. GPU is settled: RTX 4050,
  6 GB, so 7B-class at 4-bit.
