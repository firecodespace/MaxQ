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

---

## Backend completion — 2026-11-12, while Shiva is offline

Report in the `AGENTS.md` §9 format. You said to take over his lane and finish the backend
before the frontend rebuild, in the order in `status/backend_remaining.md`. All seven items on
that list are done. The eighth is blocked on a decision only you can make.

### Done

**0. Landed Shiva's pushed physics onto `main`** (`b088af1`, merge of `f05c50b`).
`main` had no APPROACH or DOCK phase, no HCW relative motion, no `solve_phasing_orbit` and no
best-known runner — all of it was sitting on his branch. Clean merge, and his unpushed
`s1_burn_frac` work merges on top. This changed the picture: the chain flies end to end and
**C09 and C10 now evaluate and pass** on a real ISS attempt. Nine of ten constraints are live;
only C05 (weather) is unevaluated, and there is no weather data source to evaluate it with.

**1. The verifier states `evaluated` explicitly** (`382031e`, `verifier_version`
`ver-f0ab2e4fa0c1` → `ver-4b49566ae7b5`). `contracts/validate.py` had been detecting an
unchecked constraint by *string-matching a note prefix*. Feasibility is now exactly
`all(c["pass"] for c in report if c["evaluated"])`. The prefix match survives only as a
read-only fallback for attempts recorded before the flag — those files are evidence and are
not rewritten, which is also why `evaluated` is not in the schema's `required` list.
This was the 🔴 item that had been pending with you; it was blocking two other lanes.

**2. Split `prop_used_kg` into ascent and on-orbit** (`fb67778`, → `ver-35cac73cafe9`).
This is why "minimum fuel" did not work. Measured on cargo_iss / falcon9_block5, sweeping
phasing revs 1→20:

| | value |
|---|---|
| ascent propellant | **constant** at 477,998.2 kg |
| on-orbit propellant | 1,104 → 11,666 kg, a **10.6×** swing |
| as a share of the summed figure | 2.2 % |
| fuel-term leverage, old → new | 0.0216 → **0.1140 (5.3×)** |

The fuel signal was always there; one ascent-scale reference buried it. `metrics` now carry
`prop_ascent_kg` and `prop_on_orbit_kg`, and the objective gains `w_prop_orbit` and
`prop_orbit_ref_kg`.

**Stated plainly, because it is not finished:** at the default weights the time term still has
**5.1× the leverage** of the fuel term. That ratio is a mission-design preference, not
something to tune into the verifier, and it now has a dial. Setting it is yours.

**3. Per-vehicle fidelity tolerances** (`4a70b4d`). `TOLERANCE_PCT` was one global constant
while the script's own docstring claimed "tolerance per vehicle (default 30 %)" — a wrong
number and a behaviour that did not exist. A vehicle may now state `sim_tolerance_pct` and
**must justify it in its `notes`**; I verified the guard bites by setting falcon9's band to
5 %, inside its measured +11.2 % error, and watching the suite reject it.

**No vehicle's tolerance was changed.** Picking per-vehicle numbers by looking at each
vehicle's measured error is fitting the test to the result, which is the one thing that script
says it will not do. And the thing most likely to be misread is now written down: three
vehicles passing at ±35 % records the model's error, it does not make the library accurate
enough to benchmark against. Tightening the constant would drop vehicles, not improve the
model.

**4. WP2-12 benchmark suite, protocol, bounds and Hall of Fame** (`b6f4c2a`).
24 missions, declared not generated. Four (profile, vehicle, pad) combinations × three windows
× two payloads. All three vehicles including `atlas_v_401`, the one the simulator models worst
at −25 % — a suite of only missions we can already solve measures nothing. Payloads are
fractions of the **published** figure, never the simulated one.

The split is eight missions each, **partitioned by launch window so no window appears in two
splits** — a system cannot have been tuned on the phasing geometry it is tested on. Every
split still carries all three vehicles, both profiles, and a phase-angle span over 120°.

`protocol_hash` is `sha256:b85b5c0b890fabc4…` and covers the full mission_requests, not just
their ids: an id is a label, and a payload changing underneath one would silently change what
every recorded cost means. It is pinned in a test, so a protocol change fails the suite instead
of quietly invalidating results. **That pin is your 🔴 gate** — the split is now fixed and
hashed, as the handoff requires.

Analytic lower bounds: lossless ascent Δv, rocket equation on the payload alone at the best
stage Isp, Hohmann half-period, and ascent time bounded by the profile's acceleration limit —
so a crew mission's 4 g cap shows up as a real floor on time. Loose by construction and
labelled a floor, not a target. Tests assert it sits below a measured feasible cost and that
it never calls the simulator.

The Hall of Fame refuses a protocol hash that is not the live one and refuses infeasible
attempts outright. The module says plainly that this stops accidents and makes tampering
detectable but **is not a security boundary** — any in-process code could call
`open_for_runner` itself.

**5. WP2-10 experience memory** (`a88f6da`). `features-v1`: 14 hand-built dimensions,
fixed declared ranges, hashed, no embedding model, fully offline. Two decisions worth your
attention, both in `docs/decisions.md`:

- **Not Qdrant.** A few thousand 14-dim vectors make exact numpy search faster than an index,
  and approximate search can silently return a worse neighbour — a confound inside the one
  thing being measured. `memory.Store` is the seam if you want the swap; it adds a pinned
  dependency, so it is your call.
- **Three features the handoff lists are omitted**, each with its reason stated: weather and
  Kp have no data source anywhere in the project, and `min_miss_km` exists for only 8 windows
  against one trajectory family. A 0 would be worse than an omission — in a vector 0 is not
  "unknown", it is a specific point that makes unrelated missions look similar.

The phase angle is encoded as (sin, cos) because it is circular. Measured: 359° and 1° land
0.035 apart; linearly they would have been the most distant pair in the space, so retrieval
would have returned the least similar window as the most similar.

`cma_memory` runs in the benchmark with a **leakage guard** that inspects the missions actually
in the memory file and refuses to measure any split they belong to.

**No claim that memory helps.** It is implemented. The `cma_cold` vs `cma_memory` comparison
is what would justify it, and that needs best-known first.

**6. WP2-11 tool layer and the no-model fallback agent** (`a9dc6d1`). The six tools §8 names,
with their schemas declared in the shape a tool-use API wants — so when you pick a model the
prompt side is already pinned to the implementation. `OptimizerAgent` is the fallback §8
requires so the UI, benchmark and demo never depend on a GPU: a five-step policy written out
in full in its docstring and asserted by a test, because its numbers are the baseline an LLM
planner has to beat. If it were tuned, the comparison would measure the tuning.

`test_agent_cannot_write_verifier` asserts four things: no tool takes a path, filename, module
or anything evaluable; the one filesystem write resolves its path and refuses anything outside
`data/attempts/`; the agent package never imports from `benchmarks/` at all, because an agent
that could read best-known could tune to it; and the verifier is reached only through
`verify()`. The honest claim is "the agent's only interface cannot do it", not "the process
cannot do it".

**7. WP2-15 analytic orbit raising** (`fd65528`). `MissionEnv` flew *every* profile down the
rendezvous path, so a GEO delivery was scored on a phase angle to a satellite the payload was
never going to meet. Now closed-form: Hohmann apogee raise plus one combined
circularise-and-plane-change burn.

| | computed | published / textbook |
|---|---|---|
| GEO altitude | 35,786.0 km | 35,786 km |
| GPS altitude | 20,183.6 km | ~20,180 km |
| Cape→GEO perigee kick | 2,454.6 m/s | ~2,440 m/s |
| Cape→GEO apogee kick | 1,837.9 m/s | ~1,800 m/s |

Combining the plane change into the apogee burn saves **1,156 m/s** — a quarter of the
post-insertion budget. Both degenerate cases fall out of the general formula exactly. GPS is
not GEO with different numbers: from the Cape at 28.56° to a 55° constellation you *raise*
inclination, and there is a test for it.

C09 and C10 come back `evaluated: false` on a transfer mission — which item 1 is what made
expressible.

### Files changed

New: `benchmarks/{suite,protocol,lower_bound,hof,runner}.py`, `memory/{features,store,warm_start}.py`,
`agent/{budget,tools,optimizer_agent}.py`, `mission/transfer.py`, `status/backend_remaining.md`,
and tests under `tests/{benchmarks,memory,agent,mission,env}/`.

Changed: `verifier/core.py`, `contracts/{validate,attempt.schema,mission_request.schema,session.schema,vehicle.schema,mission_trajectory.schema}`,
`env/mission_env.py`, `optimize/cma_optimizer.py`, `scripts/validate_rocket_library.py`,
`tests/ascent/test_sim.py`, `docs/decisions.md`.

### How to run

```bash
python -m benchmarks.runner splits          # the protocol, the splits and the hash
python -m benchmarks.runner bounds          # analytic floors, no simulation
python -m benchmarks.runner memory-build --split train
python -m benchmarks.runner run cma_cold --split val
python -m benchmarks.runner run cma_memory --split val
python -m benchmarks.runner table
```

### Contract changes

Four, all recorded in `docs/decisions.md` with reasons. None invalidates an existing file.

1. `attempt.schema.json`: `constraint_report[].evaluated` (optional — historic attempts are
   evidence); `metrics.prop_ascent_kg` and `metrics.prop_on_orbit_kg`; phase enum gains
   `transfer` and `circularise`.
2. `mission_request.schema.json`: `objective.w_prop_orbit` and `objective.prop_orbit_ref_kg`.
   The validator refuses a weight set without its reference — a dial wired to nothing.
3. `session.schema.json`: `agent_step` no longer requires `attempt_id`. The tool layer makes
   mission-level steps unavoidable — `get_constraints` and `query_memory` run before any
   attempt exists — so the old rule made a contract-valid agent trace impossible. `kind` and
   `summary` are still required, and an `attempt_id` naming no running attempt is still refused.
4. `vehicle.schema.json`: `sim_tolerance_pct`, with a test requiring its justification.

Two bugs fixed on the way: `tests/ascent/test_sim.py` asserted that a 3.6 g throttled flight
reaches 160 km perigee on the vehicle's *default* pitch profile. It reaches 105.7 km, correctly
— less thrust on a schedule tuned for full thrust. Split into the two claims it conflated; the
"can a throttled ascent reach orbit" half now uses a profile found by our own CMA-ES (202.9 km
at 3.600 g, seed 42, 900 evals), recorded as a search result rather than a magic constant.

### Verified

`ruff check .` clean. `python -m contracts.validate` → **59/59 valid**.
`pytest -m "not slow"` (excluding the two server/web cases that need a loopback socket outside
this sandbox) → **635 passed**, up from 449, with 186 new tests.

Also re-ran `scripts/validate_rocket_library.py` after the merge: antares +15.0 %, atlas_v
−25.0 %, falcon9 +11.2 %, all PASS at ±35 %, `sim_version ascent-dbc9a3aede21`. **These
reproduce Shiva's reported figures exactly from a clean checkout — his report was accurate.**

### Next, blocked, needs from someone

**Running now, in the background:** the best-known reference search on the train split, 1,500
evaluations × 7 seeds × 8 missions. One mission done so far
(`crew_f9_w0_light`, cost 1.160000 against an analytic floor of 0.1466). It is slow — ~21 min
per mission — so it will not be finished when you read this. Until it is, every `gap %` in the
benchmark table is correctly `null`, not 0.

**Then, with no further decisions needed from you:** `memory-build --split train`, then
`cma_cold` and `cma_memory` on `val`. That is the measurement that either justifies WP2-10 or
does not, and I will report whichever it shows.

**🔴 Needs you — one real decision.** The **open-weight model and licence** for the LLM planner
(WP2-11's second half). On a 6 GB RTX 4050 that realistically means a quantised 7–8B class
model, but choosing it is not mine to make. Everything else for it is built: the tool layer,
the schemas, the budget enforcement, the trace, and the fallback it has to beat.

**🟡 Worth your attention, not blocking.**
- The **fuel/time weight ratio** (item 2). Time currently outweighs fuel 5.1:1 on the margin.
- **Qdrant or not** (item 5).
- **C05 weather.** There is no data source, so it will report "not evaluated" forever. Either
  wire a cited source or state in the contract that it is out of scope. Right now it reads as
  an oversight rather than a decision.

**Left open deliberately.** WP2-15's GEO launch *window timing* is still the placeholder the
window data's own `_note` describes. Real GTO windows need the node and perigee-argument
alignment that puts apogee over the target longitude — separate work from the burn physics, and
nobody is blocked on it. WP2-13 fine-tuning and WP2-14 RL are Tier C, need a GPU, and need the
benchmark to mean anything first.

**For Shiva.** I was in five of his directories: `verifier/`, `env/`, `optimize/`, `mission/`
and `ascent/` (tests only). Every change is listed above and in `status/shiva.md`. The ones he
will care about: `verifier_version` moved twice, `env/mission_env.py` gained a transfer branch
and the propellant split, and `optimize/cma_optimizer.py` gained `plan_from_x` / `x_from_plan`
plus three optional memory arguments. His unpushed work should merge on top; the propellant
split and the transfer branch are the two places to look for a conflict.
