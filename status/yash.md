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

## 2026-11-11 - WP2-06 (mission API, streaming, replay)

**Done**
- `server/`: FastAPI app with the mission lifecycle (create, launch, abort), attempts, a WebSocket
  live stream, and replay of any recorded session. `make server` runs it; docs at `/docs`.
- The live stream and the recording are the same file: the optimizer writes `session.jsonl`, the
  server tails it live and serves it for replay. Asserted by test, not just by design.
- Budgets are real: runs are subprocesses, so `max_wall_s` terminates one. A 1 s budget is honoured.
- Library endpoints for the wizard serve the cited vehicles and pads WITH each vehicle's measured
  `sim_validation`, so a card can never quote a published payload without the measured gap.
- 14 server tests (6 fast, 8 slow). Whole suite green.

**Found while integrating (reported to Shiva with `scripts/probe_feasibility.py`)**
- crew_iss cannot be feasible at any payload: no throttling, so peak g is 4.38-4.64 against a 4.0 g
  limit. cargo_iss is feasible (cost 1.084), so the chain itself is sound.
- the placeholder search (now `optimize/fixed_sigma_es.py`) clips the pitch program to the archetype bounds, which alone keeps C01
  unreachable for real vehicles.

**Next**
- WP2-07 wizard and WP2-08 mission control, both against this API. The wizard's constraints step can
  use `/api/library/windows` and should show the crew-mission limitation above rather than offering a
  mission that cannot pass.

## Truthful labels + real CMA-ES — 2026-11-12 (claude-code, on Yash's instruction)

**Done.** Two commits, labels first.

1. `refactor: make every label match the implementation` — the WP2-05 search was named
   `cma_optimizer.py`, tagged attempts `optimizer-cma` and was reported as CMA-ES; it imported only
   numpy and ran a fixed-sigma Gaussian sampler. Renamed to `optimize/fixed_sigma_es.py` /
   `search-fixed-sigma-es`. `best_known_gap_pct` 0.0 -> null everywhere (no benchmark exists;
   schema already allowed null). `agent_step` is kind `note` with no fabricated `tool` field. The
   run declares `search: "placeholder"` in `mission_started` and the UI badge reads it from that
   event, so the badge cannot drift from the code. AGENTS.md gained charter items 11 and 12.
   `ascent/optimize.py` (WP-06) genuinely uses `cma` — its labels were already right, untouched.
2. `feat(wp2-05): real CMA-ES` — `optimize/search_core.py` holds both algorithms as pure functions
   so the comparison test runs the real code; `optimize/cma_optimizer.py` is the mission-level
   CMA-ES; the server now runs it.

**Measured** (not claimed):
- Rotated, condition-1e6, 6-D quadratic, budget 3000, seed 1: CMA-ES **1.96e-14** vs fixed-sigma
  **3.85e3**. Sigma falls and the covariance condition reaches 1.0e6, both recorded per generation.
- cargo_iss / falcon9_block5, 100 attempts, seed 42, same bounds: feasible **13/100 -> 38/100**.
  Best cost **unchanged at 1.082**.
- Wider pitch bounds cost 38/100 -> 34/100 on this mission with no cost improvement. Kept anyway,
  because the narrow archetype bounds exclude every feasible solution for some real vehicles.

**The finding that matters more than any of this.** Across 100 feasible-or-not attempts, every
feasible attempt's objective lies inside **0.213%** (prop 503,269–504,406 kg; time 13,037–13,042 s).
Stages burn to depletion at constant thrust and phasing is a fixed coast, so the thing being
minimised barely responds to the plan. A better optimiser cannot produce a better result here
because there is almost nothing to optimise. Throttling and a real approach/docking phase (WP2-03,
Shiva's lane) have to land before optimiser quality is measurable at all.

**Contract bug found and fixed.** `_check_attempt` required `feasible == all(constraint.pass)`,
which contradicts the verifier: an unevaluated constraint reports `pass=False` with a note and does
not block feasibility. No feasible attempt had ever reached the validator, so this was latent until
the first feasible CMA-ES attempt. The validator now excludes constraints marked unevaluated.

**Needs you (🔴).** The validator identifies "unevaluated" by the note prefix. The clean fix is an
explicit `evaluated` boolean per constraint, which means editing `verifier/` and bumping
`verifier_version`.

**Lane note.** `optimize/` is Shiva's in HANDOFF_PHASE2; this was done on your direct instruction
(charter §2 precedence 1). His `status/shiva.md` WP2-05 entry carries a dated correction rather
than a rewrite. He should be told before he next touches `optimize/`.

**Tests.** `tests/optimize/` 17 passed (6 baseline honesty guards, 11 CMA-ES incl. adaptation, the
rotated-quadratic comparison, determinism, and budget-in-evaluations). Full suite **445 passed**,
ruff clean.

**Not started, as instructed:** memory (WP2-10), LLM agent (WP2-11). Benchmark (WP2-12) is the next
thing that would make any of these numbers comparable.
