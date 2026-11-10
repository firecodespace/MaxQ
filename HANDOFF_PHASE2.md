# MaxQ Phase 2 — Autonomous Mission AI (Launch → Docking)

> An AI agent plans, flies and verifies a full mission (launch → orbit → phasing → approach → docking) against real orbital data, retries many times, remembers what worked, and shows everything live in a 3D mission-control UI.
> **Project Lead: Yash.** Lane owner: **Shiva.** Any AI editor or agent (Claude Code, ChatGPT/Codex, Antigravity, Gemini, Cursor, …) works on behalf of one of them.
> Builds on MaxQ v1 (real catalog, real ISS/GPS windows, ascent sim, risk screening, 2D UI). Created 2026-11-11.

---

## 0. Reading order

| File | Purpose |
|---|---|
| `AGENTS.md` | Universal charter: authority, freedom levels (Green/Yellow/Red), quality bar, git etiquette. **Unchanged, plus §2 below.** |
| `HANDOFF.md` (v1) | Existing contracts, physics reference, templates (§10). Still valid. |
| `HANDOFF_PHASE2.md` (this file) | Phase 2 scope, new contracts, work packages, UI, AI and memory plans |
| `status/BOARD.md`, `status/<name>.md`, `docs/decisions.md`, `docs/proposals/` | Same as v1. Add Phase 2 rows to the board. |

**Chat-only agents (paste mode):** context pack = `AGENTS.md` + this file's §2 (charter additions) + §6 (contracts) + the WP in §9 + files to modify.

---

## 1. Vision and honesty guardrails

**Goal.** A user opens the mission wizard, chooses mission type → payload → rocket → pad, sees the live constraints, then presses **Launch**. An AI agent plans and executes the whole mission in simulation, runs many attempts, and converges toward a **best-known benchmark** for minimum fuel and minimum time while passing every constraint. The UI shows each attempt, its stats, the agent's decision trace, and what it retrieved from memory.

**What we will claim**
- Real data: live orbits (CelesTrak), real ISS/GPS windows, live Kp, historical ISS TLEs for residual correction, public rocket and pad specs (cited, approximate).
- An agent that improves with experience, measured by a held-out benchmark with ablations.
- An offline-capable experience memory (vector store).

**What we will not claim**
- "Unbeatable" or "optimal". We claim **best-known**, with the gap to a long-budget global-search reference and to analytic lower bounds reported.
- Flight-grade accuracy, real docking GNC, or operational use. Rendezvous uses simplified relative-motion dynamics with illustrative tolerances.
- That the AI "does the physics". The simulator computes the physics; the agent decides; a deterministic verifier judges.
- That fine-tuning helps. We measure it against an optimizer-only baseline and report negative results.

---

## 2. Charter additions (append to `AGENTS.md`)

Integrity rules for work where AI sits inside the loop. They apply to every agent and human.

1. **The verifier and benchmark are ground truth.** `verifier/` and `benchmarks/` are read-only to any agent being trained or evaluated, and to any code path the agent controls at runtime. Changes need Yash's OK (🔴) and must bump `verifier_version`. Old attempts are re-scored before comparison.
2. **No leakage.** Never tune, prompt, or fine-tune on the **test** split. Use train/val for development; run test once per reported result.
3. **Memory is a hint, not truth.** Retrieved experiences only warm-start. Every retrieved plan is re-simulated and re-verified before it counts.
4. **Version everything.** Each attempt records `sim_version`, `verifier_version`, agent id/model/version, seed, and config. Memory records older than the current `sim_version` are filtered out or re-scored.
5. **Reproducibility.** Same mission + seed + versions ⇒ same result. Log seeds; no hidden nondeterminism.
6. **Cite real data.** Rocket, pad, and constraint numbers come from cited sources (manufacturer guides, agency pages), fetched and checked, not recalled. Store `sources[]` and `confidence`; mark approximate; note conflicts.
7. **No operational claims.** Label sim output "SIM · reduced fidelity" and constraint values "illustrative" unless sourced.
8. **Show decision traces, not raw hidden reasoning.** The trace holds the agent's stated plan, tool calls, key numbers, and decisions. Don't present model free-text as fact.
9. **Budgets.** Every agent run has `max_attempts` and `max_wall_s`. Enforce them in code.
10. **Models and licenses.** Use open-weight models only after checking the license and recording it in `docs/decisions.md` (🔴 for the first choice). Keep secrets in env vars.

---

## 3. Governance and lanes

| Person | Role | Lane (default folders) |
|---|---|---|
| **Yash** | **Project Lead** + lane owner | `agent/`, `memory/`, `server/`, `web/` (UI), `data/rockets/`, `data/pads/`, `scripts/`, `catalog/`, `mission/` |
| **Shiva** | Lane owner | `env/`, `ascent/`, `rendezvous/`, `verifier/` (gated), `optimize/`, `train/` |
| **AI agent** | Acts for a human | The invoking human's lane |

**Gated (Yash approves):** `contracts/`, `core/`, `verifier/` after milestone M1, `benchmarks/` protocol, model/license choice, public deploy that exposes paid or GPU endpoints. Yash may reassign any WP to any person or agent.

Decision rights are as in `AGENTS.md` §3. New Red items: verifier thresholds, benchmark protocol, train/val/test splits, model/license choice.

Suggested `CODEOWNERS` (optional, enables enforcement with branch protection): `/verifier/ @<yash-handle>`, `/benchmarks/ @<yash-handle>`, `/contracts/ @<yash-handle>`.

---

## 4. Architecture

```
 Mission Wizard (UI) ──► POST /missions ──► mission_request.json
                                   │
                                   ▼
        ┌───────────────  Agent Orchestrator (agent/)  ───────────────┐
        │  LLM (open model) or deterministic OptimizerAgent fallback    │
        │  tools: propose_plan · run_sim · get_constraints ·            │
        │         query_memory · run_optimizer · commit_attempt         │
        └───────┬───────────────┬───────────────┬───────────────┬──────┘
                ▼               ▼               ▼               ▼
        env/ MissionEnv   optimize/ CMA-ES   memory/ Qdrant   verifier/
        ASCENT→PARKING→   BO / search        (experience      (deterministic
        PHASING→APPROACH  over plan params    retrieval)       constraints+cost)
        →DOCK
                └───────────────┴──────► attempt.json ◄────────────┘
                                   │
                         session stream (WS/SSE or JSONL replay)
                                   ▼
        Mission Control UI: 3D scene · phase rail · constraints · attempts ·
                            decision trace · memory · telemetry · benchmark
```

**Mission phases:** `ASCENT` (existing 2D sim, wrapped) → `PARKING` → `PHASING` (burns to close the phase angle to the ISS) → `APPROACH` (relative-motion waypoints, corridor) → `DOCK`.

**Two run modes (both required):**
- **Live:** backend runs the agent; UI streams events over WebSocket/SSE. Needs a server (and a GPU if the LLM is local).
- **Replay:** the UI plays a recorded `session.jsonl`. This keeps a static public deploy working without a backend. Record real sessions; never fake them.

---

## 5. Constraints, objective, benchmark

### 5.1 Hard constraints (illustrative defaults; real values come from cited sources where possible)

| ID | Constraint | Default | Notes |
|---|---|---|---|
| C01 | Orbit insertion | perigee ≥ 160 km, ecc small | from ascent |
| C02 | Max dynamic pressure | ≤ rocket `max_q_limit_kpa` | per vehicle |
| C03 | Max acceleration | crew ≤ 4.0 g, cargo ≤ 6.0 g | illustrative |
| C04 | Launch window | within tolerance of a real window; plane error ≤ limit | from `windows.json` |
| C05 | Weather go/no-go | per-profile thresholds on forecast | Open-Meteo; illustrative |
| C06 | Conjunction risk | status ≠ `no-go` across ascent, parking, approach | from screening |
| C07 | Propellant reserve | ≥ 2% ascent, ≥ 5% orbital | |
| C08 | Mission time budget | ≤ profile limit | configurable |
| C09 | Approach corridor / keep-out | stay in cone; no keep-out violation except on corridor | illustrative |
| C10 | Final approach | closing speed ≤ 0.1 m/s, lateral offset ≤ 0.1 m | illustrative; verify against public sources before citing |

### 5.2 Cost (lower is better)

```
cost = Σ_violated  (1000 + 1000 · normalized_violation)        # infeasible always loses
     + w_fuel · (prop_used_kg / fuel_ref_kg)
     + w_time · (time_to_dock_s / time_ref_s)                    # only if feasible
```
Fuel and time trade off (fast rendezvous costs fuel), so also report the **Pareto front** of (fuel, time) over feasible attempts.

### 5.3 Benchmark ("best-known", not "unbeatable")

For each mission in the suite:
- **Analytic lower bound:** rocket-equation ideal + Hohmann/phasing Δv bound (a floor that cannot be beaten).
- **Best-known:** the best feasible cost from a long-budget global search (e.g. thousands of sims, several seeds) run by the benchmark runner.
- **Agent result:** best feasible cost within the agent's budget; report the **gap %** to best-known.
- A mission counts as **converged** when the agent is within ε% (default 2%) of best-known, feasible, across ≥ 3 seeds.
- **Hall of Fame:** `benchmarks/hof.json` is updated only by the benchmark runner after verification, never by an agent.

---

## 6. Data contracts (new; v1 contracts remain valid)

All have `"schema_version": 1`. Schemas in `contracts/*.schema.json`; `data/dummy/` examples. Example times: `1794372400` = 2026-11-11T04:46:40Z.

### 6.1 `mission_request.json`
```json
{
  "schema_version": 1, "id": "msn_crew_iss_0001", "profile": "crew_iss",
  "payload": {"mass_kg": 12000, "crew": 4},
  "rocket_id": "<id from data/rockets>", "pad_id": "<id from data/pads>",
  "target": {"norad_id": 25544},
  "window_id": "w0", "constraints_profile": "crew_default",
  "objective": {"w_fuel": 1.0, "w_time": 0.5, "fuel_ref_kg": 1.0, "time_ref_s": 1.0},
  "budget": {"max_attempts": 200, "max_wall_s": 1800}, "seed": 7
}
```

### 6.2 `attempt.json` (one full launch-to-dock run)
```json
{
  "schema_version": 1, "id": "att_000123", "mission_id": "msn_crew_iss_0001",
  "agent": {"id": "optimizer-cma", "model": null, "version": "0.1.0"},
  "sim_version": "env-0.1.0", "verifier_version": "ver-0.1.0", "seed": 123,
  "plan": {
    "launch_t_unix": 1794372400,
    "pitch_program": [0.0, 0.0],
    "parking_alt_km": 200,
    "burns": [{"phase": "phasing", "t_s": 5400, "dv_mps": [60.9, 0.0, 0.0], "frame": "LVLH"}],
    "waypoints": [{"name": "hold_1km", "range_m": 1000}, {"name": "hold_200m", "range_m": 200}]
  },
  "phases": [{"name": "ascent", "t0_s": 0, "t1_s": 520, "prop_kg": 0.0}],
  "metrics": {"prop_used_kg": 0.0, "total_dv_mps": 0.0, "time_to_dock_s": 0.0},
  "constraint_report": [{"id": "C01", "name": "orbit_insertion", "value": 199.6, "limit": 160, "op": ">=", "pass": true, "margin": 39.6}],
  "feasible": true, "cost": 1.23, "best_known_gap_pct": 4.2,
  "trajectory_ref": "data/attempts/att_000123.traj.json", "wall_s": 1.8, "status": "finished"
}
```
`trajectory_ref` points to a downsampled multi-phase trajectory (extends the v1 `trajectory.json` shape with `phase` per sample and relative-motion fields `rel_pos_m[3]`, `rel_vel_mps[3]` during approach).

### 6.3 `session.jsonl` (stream and replay; one JSON object per line)
Event `type` values: `mission_started`, `attempt_started`, `memory_hit`, `agent_step`, `phase_changed`, `telemetry` (1–5 Hz sim time), `constraint_update`, `attempt_finished`, `benchmark_update`, `mission_finished`.
```json
{"t_wall": 12.4, "type": "agent_step", "attempt_id": "att_000123", "kind": "decision",
 "summary": "Widened phasing burn window after C08 margin fell to 3%",
 "data": {"tool": "propose_plan", "key_numbers": {"time_margin_pct": 3.0}}}
```
`kind` ∈ `plan | tool_call | tool_result | memory_hit | decision | note`.

### 6.4 `memory_record` (Qdrant point)
```json
{
  "id": "mem_000045", "vector_spec": "features-v1", "vector": [0.0],
  "payload": {"mission_id": "…", "attempt_id": "…", "profile": "crew_iss", "rocket_id": "…",
              "feasible": true, "cost": 1.23, "plan": {}, "failure_modes": [],
              "sim_version": "env-0.1.0", "verifier_version": "ver-0.1.0", "created_utc": "…"}
}
```

### 6.5 `data/rockets/<id>.json` and `data/pads/<id>.json`
```json
{
  "schema_version": 1, "id": "<slug>", "name": "<vehicle name>", "kind": "real",
  "crew_rated": false, "max_q_limit_kpa": null,
  "stages": [{"dry_kg": 0, "prop_kg": 0, "thrust_n": 0, "isp_s": 0, "cd": 0.3, "area_m2": 0}],
  "payload_to_leo_kg_published": null,
  "sources": [{"title": "…", "url": "…", "retrieved_utc": "…"}],
  "confidence": "approximate", "notes": "conflicts between sources, assumptions"
}
```
Pad entry: `id`, `name`, `lat_deg`, `lon_deg`, `supported_rocket_ids[]`, `azimuth_range_deg` (public range-safety info where available, else null), `sources[]`. Keep the three v1 fictional archetypes (`kind: "archetype"`) as a fallback.

### 6.6 `benchmarks/benchmark.json`
```json
{
  "schema_version": 1, "suite_id": "suite_v1", "protocol_hash": "<sha256>",
  "splits": {"train": ["…"], "val": ["…"], "test": ["…"]},
  "best_known": {"msn_…": {"cost": 1.2, "attempt_id": "…", "budget_sims": 5000}},
  "lower_bound": {"msn_…": {"dv_mps": 0.0}},
  "systems": {"cma_cold": {}, "cma_memory": {}, "llm_base": {}, "llm_ft": {}}
}
```

---

## 7. Physics notes and acceptance tests

Reuse the v1 reference (`HANDOFF.md` §7). Additions:

- **Relative motion (Hill–Clohessy–Wiltshire):** mean motion `n = √(μ/a³)`; closed-form state-transition matrix in the LVLH frame; two-impulse transfers solved from the matrix.
- **Phasing:** choose a phasing-orbit period and number of revolutions to close the phase angle to the target; the agent explores this as a plan parameter.
- **Approach:** waypoint holds (e.g. 1 km, 200 m), then final approach inside the corridor at the speed limit.

| Test | Expected |
|---|---|
| `test_hohmann_200_to_410km` | total ≈ 121 m/s (accept 119–124) |
| `test_hcw_vs_kepler` | closed-form vs numerical Keplerian propagation within 1% for separations ≲ 1 km over a fraction of an orbit |
| `test_env_deterministic` | same request + seed + versions ⇒ identical attempt |
| `test_verifier_deterministic` | same attempt ⇒ identical constraint report |
| `test_verifier_catches_violation` | a hand-built infeasible attempt gets `feasible=false` and the right constraint IDs |
| `test_cost_orders_correctly` | any feasible attempt beats any infeasible one |
| `test_rocket_library_validation` | sim payload-to-LEO within a stated tolerance of the published value (tolerance recorded per rocket; report misses honestly) |
| `test_contracts_validate` | all files in `data/dummy/`, `data/rockets/`, `data/pads/`, `data/attempts/` validate |
| `test_memory_filters_stale` | records with an older `sim_version` are not returned for warm-start |
| `test_agent_cannot_write_verifier` | tool layer exposes no write path to `verifier/` or `benchmarks/` |

Performance target (measure, then adjust): ≥ 5 full-mission evaluations per second on one CPU core for the optimizer loop; vectorize or simplify if below.

---

## 8. AI model plan

**Agent stack**
1. **Planner (open-weight LLM, tool-using):** reads the mission and constraints, queries memory, proposes plan parameters, interprets verifier feedback, decides when to stop. Runs through a standard open-source inference server; model choice is 🔴 (license check).
2. **Search (CMA-ES / Bayesian optimization):** refines plan parameters with the simulator in the loop. The planner can call it as a tool.
3. **Guidance policy (RL, stretch):** a small neural policy for ascent or approach; compared to the optimizer.
4. **Verifier (deterministic code, never learned):** pass/fail and cost.

**Deterministic fallback agent:** `OptimizerAgent` emits the same trace and session events using only the optimizer and memory. The UI, benchmark, and public demo must work with it, so nothing blocks on a GPU or model.

**Tool schema** (names stable; arguments in `agent/tools.py`): `propose_plan`, `run_sim`, `get_constraints`, `query_memory`, `run_optimizer`, `commit_attempt`. Each call is logged to the trace; no tool can write to `verifier/` or `benchmarks/`.

**Fine-tuning (Tier C, needs a GPU):**
- Data: (a) missions solved by the optimizer (context → plan parameters); (b) tool-use transcripts of the best feasible runs (rejection-sampling fine-tune). Train split only.
- Method: LoRA/SFT with standard open-source tooling.
- Evaluation: held-out test split, four systems: `cma_cold`, `cma_memory`, `llm_base`, `llm_ft`.
- Metrics: sims-to-first-feasible, best feasible cost gap to best-known, success rate within budget, wall time.
- Report the ablation table whatever it shows. The LLM's likely value is fewer simulations, orchestration, and explanation, not beating the optimizer on raw cost.

---

## 9. Experience memory (Qdrant, offline-capable)

- **Role:** retrieval of similar past missions to warm-start search (case-based reasoning). It becomes RAG only if an LLM consumes the retrieved cases as context.
- **Vector:** hand-built normalized features (`features-v1`): profile one-hot, target altitude/inclination, payload ÷ rocket capacity, rocket thrust-to-weight and Δv capability, pad latitude, window phase angle and plane error, weather metrics, Kp, minimum miss distance, objective weight ratio. Fixed normalization stats stored with the spec. No embedding model needed offline.
- **Payload filters:** `profile`, `rocket_id`, `feasible`, `sim_version`.
- **Warm-start strategies:** (1) center the optimizer on the best neighbor; (2) bound the search near neighbors; (3) give the LLM neighbors as examples. Store failures with `failure_modes` to steer away.
- **Deployment:** use the Python client's local on-disk mode for offline use; check the Qdrant docs. Exact search is available; the default is approximate. Quantization is lossy. Don't call it "lossless" or "onboard flight computer". Call it "offline-capable experience memory".
- **Proof of value:** benchmark `cma_cold` vs `cma_memory` on the held-out split (sims-to-feasible and final gap).

---

## 10. UI spec

**Wizard (6 steps):** Mission type (LEO insertion → Crew to ISS / Cargo to ISS; later GPS/GEO) → Payload → Rocket (real vehicles filtered by capability, crew rating, and pad compatibility; each card shows sources and a "SIM-validated within X%" badge) → Pad → Constraints (live: window, weather, Kp, risk, with go/no-go) → Launch.

**Mission Control layout**
```
┌───────────── 3D scene (Earth cam ⇄ chase cam ⇄ onboard cam) ─────────┬─ Phase rail: ASCENT ▸ PARKING ▸ PHASING ▸ APPROACH ▸ DOCK ─┐
│ Earth, live satellites, trajectory ribbon, rocket/capsule, ISS,       ├─ Constraints C01…C10: value / limit / margin (green/red)   │
│ approach corridor + keep-out volume                                    ├─ Objective: fuel kg · time h · cost · gap to best-known    │
├───────────────┬──────────────────────┬─────────────────────────────────┴────────────────────────────────────────────────────────────┤
│ Attempts      │ Decision trace       │ Memory: retrieved cases, similarity, what warm-started   │ Telemetry: alt, v, q, g, mass, range, closing speed │
│ scatter (fuel │ plan / tool call /   │                                                           │                                      │
│ vs time) +    │ result / decision    │                                                           │                                      │
│ best-so-far   │ (stream)             │                                                           │                                      │
└───────────────┴──────────────────────┴───────────────────────────────────────────────────────────┴──────────────────────────────────────┘
```
- **Attempts view:** ghost trajectories for past attempts, best highlighted; click an attempt to replay it.
- **Always-visible labels:** "SIM · reduced fidelity", "illustrative limits", "verifier v…", agent id/model, live vs replay.
- **States:** live, replay, no-backend (replay only), and a clear error state when a data file or stream is missing.
- **Stack:** Three.js scene (ECI frame, Earth rotated by GMST, log depth buffer, exaggerated model in Earth view, true scale in chase cam); `satellite.js` worker for the catalog; data bound only to the contracts above. The 2D map from v1 stays as a fallback tab.

---

## 11. Work packages

Tiers: **A** foundation (must), **B** core AI + docking, **C** stretch. Time boxes are in sprints; Yash sets dates. Default owners can be reassigned.

| WP | Title | Owner | Tier |
|---|---|---|---|
| WP2-00 | Scaffold, contracts, dummy data, board rows | shared | A |
| WP2-01 | Real rocket and pad library (cited) | Yash | A |
| WP2-02 | MissionEnv core (phases, plan mode, step mode) | Shiva | A |
| WP2-03 | Phasing and rendezvous physics (HCW) | Shiva | A→B |
| WP2-04 | Verifier and cost (gated) | Shiva | A |
| WP2-05 | Baseline optimizer, attempt logging, best-known runner | Shiva | A |
| WP2-06 | API + session streaming + replay recording | Yash | A |
| WP2-07 | UI: mission wizard | Yash | A |
| WP2-08 | UI: mission control panels (constraints, attempts, trace, memory, telemetry) | Yash | A |
| WP2-09 | UI: 3D scene and cameras | Yash | B |
| WP2-10 | Experience memory + warm-start benchmark | Yash | B |
| WP2-11 | Agent orchestrator (LLM tools + OptimizerAgent fallback) | Yash | B |
| WP2-12 | Benchmark suite, protocol, ablation runner (protocol is 🔴) | shared | B |
| WP2-13 | Fine-tuning | Shiva | C |
| WP2-14 | RL guidance policy | Shiva / first free | C |
| WP2-15 | GPS/GEO profiles; replace GEO placeholder windows with real ones | Yash | C |
| WP2-16 | README, demo recording, results write-up | Yash | B |

Each WP below: **Outcome · Must · Free · Done when**.

**WP2-00 Scaffold and contracts.** *Outcome:* every lane can start without waiting. *Must:* §6 schemas + `validate`; dummy files for every new contract incl. a short recorded `session.jsonl` and one `attempt.json`; Makefile targets (`env`, `agent`, `server`, `bench`, `web`); board rows. *Free:* schema tooling. *Done when:* `make test` validates all dummies.

**WP2-01 Rocket and pad library.** *Outcome:* ≥ 3 real vehicles (including one crew-capable class) and ≥ 3 pads with cited sources. *Must:* values fetched from cited sources and checked, `confidence: approximate`, conflicts noted; the 3 fictional archetypes kept as `kind: archetype`; each vehicle gets a recorded sim-validation result (payload-to-LEO vs published). *Free:* which vehicles and pads, data format details within the contract. *Done when:* `test_rocket_library_validation` runs and results are recorded honestly.

**WP2-02 MissionEnv.** *Outcome:* one environment that runs a mission from a `mission_request` through ASCENT → PARKING → PHASING, and later APPROACH → DOCK. *Must:* deterministic; **plan mode** (evaluate a full plan in one call, for optimizers and LLM) and **step mode** (Gym-style, for RL); wraps the existing ascent sim; emits `attempt.json` + trajectory; records `sim_version`. *Free:* internal structure, integrator, parameterization. *Done when:* `test_env_deterministic` passes; a plan reaches the parking orbit using a real rocket entry.

**WP2-03 Phasing and rendezvous.** *Outcome:* the environment can close the phase angle to the ISS and fly an approach. *Must:* HCW-based relative motion; `test_hohmann_200_to_410km`, `test_hcw_vs_kepler`; approach waypoints and corridor; uses the real ISS orbit and the SGP4 residual correction where available. *Free:* planner method (Lambert-style, search, or analytic). *Done when:* a plan reaches the 200 m hold point feasibly; full docking within the illustrative final-approach limits when Tier B lands.

**WP2-04 Verifier and cost.** *Outcome:* a deterministic judge. *Must:* constraints C01–C10, cost per §5.2, `verifier_version` hash, tests in §7; no import path from agent code that can modify it; any later change is 🔴. *Free:* internal structure. *Done when:* `test_verifier_*` and `test_cost_orders_correctly` pass.

**WP2-05 Baseline optimizer and best-known runner.** *Outcome:* an optimizer-only agent plus the reference benchmark. *Must:* CMA-ES or Bayesian optimization over plan parameters; logs every attempt as `attempt.json`; a long-budget best-known runner with several seeds; seeds recorded. *Free:* algorithm choice, parameterization, parallelism. *Done when:* attempts stream into a `session.jsonl` the UI can replay; best-known values are saved with their budgets.

**WP2-06 API and streaming.** *Outcome:* the UI can start a mission and watch it live, or replay a recording. *Must:* endpoints to create a mission, launch, list attempts, and a WebSocket or SSE stream of §6.3 events; recorded-session writer; enforcement of `max_attempts` / `max_wall_s`. *Free:* framework (suggested: FastAPI), auth for local use. *Done when:* a live session and its replay produce the same UI.

**WP2-07 UI: wizard.** *Outcome:* the six-step flow in §10. *Must:* real rocket/pad data and live constraints; no fake values (dummy data labeled). *Free:* visual design, component structure.

**WP2-08 UI: mission control.** *Outcome:* the panels in §10 fed only by the session stream. *Must:* constraints checklist with margins, attempts scatter + best-so-far curve, decision trace, memory panel, telemetry, benchmark gap; live and replay modes; honesty labels.

**WP2-09 UI: 3D.** *Outcome:* the 3D scene and cameras in §10. *Must:* binds to the same contracts; 2D map remains as fallback; log depth buffer and scale handling. *Free:* Three.js structure, model art, effects.

**WP2-10 Memory.** *Outcome:* experience retrieval that measurably speeds up search. *Must:* §9 spec, `test_memory_filters_stale`, warm-start strategies, the `cma_cold` vs `cma_memory` comparison on held-out missions. *Free:* distance metric, number of neighbors, collection layout.

**WP2-11 Agent orchestrator.** *Outcome:* an LLM agent that uses the tools to run a mission, with a fallback that needs no model. *Must:* tool layer per §8, trace events per §6.3, budgets enforced, `test_agent_cannot_write_verifier`, model/license choice approved. *Free:* prompting, loop structure, serving stack.

**WP2-12 Benchmark suite.** *Outcome:* a fair, versioned evaluation. *Must:* ≥ 12 missions across profiles/windows/payloads; train/val/test split fixed and hashed (🔴 to change); runner for the four systems; results table with gaps to best-known and lower bounds; Hall of Fame updated only by the runner.

**WP2-13 Fine-tuning (C).** *Outcome:* a measured result on whether fine-tuning helps. *Must:* train split only; report the ablation table honestly.

**WP2-14 RL policy (C).** *Outcome:* a policy for ascent or approach compared against the optimizer. *Must:* same verifier and cost; held-out evaluation.

**WP2-15 GPS/GEO (C).** *Outcome:* real GPS and GEO windows and profiles; the GEO placeholder is retired. *Must:* analytic burns for transfer/plane change; labeled approximations.

**WP2-16 Docs and demo.** *Outcome:* README with architecture, fidelity statement, benchmark table, and a recorded demo session. *Must:* every number traceable to a run.

---

## 12. Milestones and lead checkpoints

| Milestone | Contents | Demo |
|---|---|---|
| **M1 Foundations** | WP2-00, 01 (partial), 02 (ascent→phasing), 04, 05, replay recording | A recorded optimizer session replays in the UI |
| **M2 Attempts visible** | WP2-06, 07, 08 | Wizard → Launch → watch attempts and constraints in replay and live |
| **M3 Memory + docking** | WP2-03 (approach), 10 | Warm-start benchmark; mission reaches the hold point / docks |
| **M4 LLM agent** | WP2-11, 12 | LLM agent runs missions with a visible decision trace; ablation v1 |
| **M5 Learning + polish** | WP2-09, 13, 14, 16 | 3D mission control; fine-tune/RL results; demo |

Yash's checkpoints: end of M1 (verifier freeze, contract sign-off), M2 (UI/data integration), M3 (benchmark protocol freeze), M4 (model choice and ablation review), M5 (release).

**When the lead is away:** take the most reversible choice, log it as `PROVISIONAL` in `docs/decisions.md`, and continue. Stop only for Red items that can't be undone.

---

## 13. Repo additions

```
env/            # Shiva: MissionEnv, phases, plan/step modes
rendezvous/     # Shiva: phasing, HCW, approach
verifier/       # Shiva (gated): constraints, cost, version hash
optimize/       # Shiva: CMA-ES/BO, best-known runner
train/          # Shiva: data export, fine-tune, RL
agent/          # Yash: orchestrator, tools, OptimizerAgent, trace
memory/         # Yash: featurizer, Qdrant client, warm-start
server/         # Yash: API, streaming, recorder
benchmarks/     # gated: suite, protocol, hof.json
web/            # Yash: wizard, mission control, 3D
data/{rockets,pads,attempts,sessions,dummy}/
```

**Stack defaults** (Green to change inside your lane if contracts and tests hold): Python 3.11+, `numpy`, `scipy`, `cma`, `pymoo`, `ortools`, FastAPI + WebSocket/SSE, `qdrant-client` (local mode), `gymnasium`, an open-source inference server for the LLM, Hugging Face `transformers`/`peft`/`trl` for fine-tuning (check current docs), Three.js + `satellite.js` for the UI.

**Compute:** decide early whether a GPU is available (`docs/decisions.md`). Without one, ship the `OptimizerAgent` path and a small local model, and record sessions for the public demo.

---

## 14. Templates

Reuse from `HANDOFF.md` v1 §10: Work Order, Session Handoff Note, Proposal, Decision log entry, Board. New:

### Experiment Card (`docs/experiments/NNN-title.md`) — for every AI experiment
```markdown
# Experiment NNN — <title>
Owner: <name/agent>   Date: <YYYY-MM-DD>   WP: <WP2-xx>
- Hypothesis: <what should improve and by how much>
- Systems compared: <cma_cold | cma_memory | llm_base | llm_ft | …>
- Data split used: <train | val>  (test only for the final report)
- Config + seeds: <model, hyperparameters, seeds, sim/verifier versions>
- Metrics: <sims-to-first-feasible, best-cost gap %, success rate, wall time>
- Result: <table or numbers; include negative results>
- Decision: <adopt | drop | retry with …>
```

---

## 15. Risks and fallbacks

| Risk | Fallback |
|---|---|
| No GPU / LLM too slow | `OptimizerAgent` path; small model; recorded sessions for public demo |
| Docking physics too hard | Ship to the 200 m hold point; label docking "approach to hold point"; add final approach later |
| Agent "cheats" the verifier | Read-only verifier, tool layer without write paths, CODEOWNERS, versioned scoring |
| Fine-tuned model doesn't beat optimizer | Report it; the story becomes memory + orchestration + explanation |
| Real rocket numbers uncertain or conflicting | Cite sources, mark approximate, record sim-validation error, never claim exact |
| Sim too slow for many attempts | Vectorize, reduce fidelity per phase, parallel workers, cache |
| Memory returns stale or wrong cases | Version filters, always re-simulate and re-verify |
| Scope creep | Tiers A/B/C; lead freezes the benchmark protocol at M3 |
| Tool disagreements | Contracts and tests win; style is Green inside a lane; Yash arbitrates |

---

## 16. Definition of done and resume bullets

- [ ] Public repo, live replay demo, and (if hardware allows) a live mode
- [ ] README: architecture, fidelity and claims statement (§1), benchmark and ablation table, demo recording
- [ ] One command per stage (`make env|agent|server|bench|web`)
- [ ] Tests green on `main`; all contracts validate; verifier frozen with a recorded version
- [ ] `status/BOARD.md` and status files current; no unresolved `PROVISIONAL` items

**Resume bullet templates (fill with measured numbers only):**
- Built an autonomous mission-planning agent that plans and verifies launch-to-docking simulations against live CelesTrak orbits, with a deterministic constraint verifier and a best-known benchmark across [N] missions.
- Cut simulations-to-first-feasible by [X%] using an offline vector-store experience memory (Qdrant) that warm-starts CMA-ES, measured on a held-out mission split.
- Compared optimizer-only, memory-augmented, and tool-using open-model agents (base vs LoRA fine-tuned): [result], within [Y%] of best-known cost.
- Shipped a 3D mission-control UI streaming attempts, constraints, agent decision traces, and memory hits live, deployed at [URL].

---

## 17. Kickoff checklist

- [ ] Phase 2 rows added to `status/BOARD.md`; `HANDOFF_PHASE2.md` committed; §2 appended to `AGENTS.md`
- [ ] New contracts + dummy data committed (WP2-00)
- [ ] GPU/compute decision logged in `docs/decisions.md`
- [ ] Benchmark split and protocol drafted for Yash's approval
- [ ] Each editor/agent session starts with: *"I'm `<name>`, working on `<WP2-xx>`"*
