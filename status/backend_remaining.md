# Backend: what is done, what is left

> **CLOSED 2026-11-12.** All seven items below were completed the same day; see the
> "Backend completion" section of  for the report, measurements and commits.
> Two things remain and both are decisions, not work: the open-weight **model choice** for the
> LLM planner (🔴 Yash) and the **fuel/time weight ratio** (🟡 Yash). The GEO launch
> *window timing* and Tier C (WP2-13 fine-tuning, WP2-14 RL) are deliberately left open.
> This file is kept as the audit of what the gap was.

Written 2026-11-12 by claude-code, while Shiva is offline and Yash asked for the backend to be
finished before the frontend rebuild. Audited against the tree, not against the handoff's claims:
every "done" below was checked by running something, and the command is named.

`main` is now at the merge of Shiva's **pushed** physics branch (`f05c50b`). His later
`s1_burn_frac` / 11-parameter work is still only on his machine and merges on top of this.

---

## Done, and verified

| WP | What | How it was checked |
|---|---|---|
| WP2-00 | Contracts + dummy data | `python -m contracts.validate` → **59/59 valid** |
| WP2-01 | 3 real vehicles + 3 pads, cited, sim-validated | `pytest tests/scripts/test_rocket_library.py` |
| WP2-02 | MissionEnv: plan mode **and** step mode | `MissionEnv.evaluate_plan` / `.reset` / `.step` all present; `pytest tests/env` |
| WP2-03 | Phasing + HCW approach, through to dock | one live attempt (below): APPROACH and DOCK both fly |
| WP2-04 | Verifier C01–C10 + cost | `pytest tests/verifier`; 9 of 10 constraints evaluate live |
| WP2-05 | CMA-ES + attempt logging + best-known runner | `optimize/cma_optimizer.py`, `optimize/best_known.py` |
| WP2-06 | API, SSE streaming, replay, enforced budgets | `server/`, running on :8000 |

**The chain works end to end.** One attempt on `falcon9_block5`, cargo-to-ISS, default plan:

```
feasible: False   cost: 4232.625
  C01 orbit_insertion      pass=False  perigee 43.4 km (limit 160.0)
  C02 max_dynamic_pressure pass=True   38617 Pa
  C03 max_acceleration     pass=False  6.015 g
  C04 launch_window        pass=True
  C05 weather_go_nogo      pass=False  not evaluated - required input not available
  C06 conjunction_risk     pass=True   risk_status='go'
  C07 propellant_reserve   pass=True   ascent 2.12%, orbital 56.0%
  C08 mission_time_budget  pass=True   54507 s
  C09 approach_corridor    pass=True   6.83 deg
  C10 final_approach       pass=True   closing 0.075 m/s, lateral offset ok
```

That attempt is infeasible, which is correct — it is the *default* plan, not a searched one. The
point is that **C09 and C10 now evaluate and pass**: the approach corridor and the final-approach
limits are being judged for real, which they were not a week ago. Only C05 is unevaluated.

---

## Left to do

### 1. Verifier: an explicit `evaluated` boolean  — small, unblocks two lanes
Today an unevaluated constraint is reported as `pass: false` plus a note beginning
"not evaluated", and `contracts/validate.py` detects it by **matching that note prefix**. That is a
string match standing in for a data field. It is now blocking two other things: the mission panels
have to render "not evaluated" differently from "failed", and the cross-version guard needs to know
which constraints were actually judged.

Add `evaluated: bool` to each constraint report, keep the note, bump `verifier_version`, and delete
the prefix matching. Touches `verifier/` + `contracts/attempt.schema.json`.

### 2. Split `prop_used_kg` into ascent and on-orbit  — the reason "minimum fuel" does not work
`env/mission_env.py:343` sums ascent propellant (hundreds of **tonnes**) with phasing and approach
propellant (hundreds of **kg**) into one number. The cost function is
`1.0·(prop/fuel_ref) + 0.5·(time/time_ref)`, so the term carrying **twice the weight supplies
almost none of the variance** — measured spreads were prop 0.741 % against time 22.932 %.

Report `prop_ascent_kg` and `prop_on_orbit_kg` separately and weight them separately. On-orbit
propellant is the part a mission plan actually controls. Until this lands, the search optimises
time and leaves fuel alone, and "minimum fuel and time" is half true.

### 3. WP2-12 — benchmark suite and protocol  *(`benchmarks/` is empty but for a `.gitkeep`)*
Nothing in the project can currently answer "is this better?". Needs: ≥12 missions across
profiles / windows / payloads, a **fixed and hashed** train/val/test split, a runner for the four
systems, a results table with gaps to best-known, and a Hall of Fame only the runner may write.

Two sub-items that have to be settled first, or the benchmark freezes a flaw into the reference:
- the split itself is 🔴 — I will propose one, you approve it, then it is hashed and fixed;
- `scripts/validate_rocket_library.py` uses a single global `TOLERANCE_PCT = 35`. A ±35 % payload
  tolerance cannot support a reference. Per-vehicle, justified in each vehicle's `notes`, tighter
  for anything used as a benchmark mission.

### 4. WP2-10 — experience memory and warm start  *(`memory/` is empty)*
`contracts/memory_record.schema.json` already exists, so the shape is settled. Needs the
`features-v1` vector per handoff §9, Qdrant in local on-disk mode (offline-capable, not "onboard"),
payload filters on `profile` / `rocket_id` / `feasible` / `sim_version`, `test_memory_filters_stale`,
and the warm-start strategies.

Its whole justification is the `cma_cold` vs `cma_memory` comparison, which **needs the benchmark
first** — which is also the order you gave. Built before the benchmark, "memory helps" is an
unfalsifiable claim.

### 5. WP2-11 — agent orchestrator  *(`agent/` is empty)*
Two halves, and only one needs a decision from you:
- **`OptimizerAgent`**, the deterministic fallback — the tool layer (`propose_plan`, `run_sim`,
  `get_constraints`, `query_memory`, `run_optimizer`, `commit_attempt`), trace events, enforced
  budgets, and `test_agent_cannot_write_verifier`. Needs no model and no GPU, and the handoff
  requires the UI, benchmark and demo to work with it. **I can build all of this.**
- **The LLM planner** — blocked on the open-weight model and licence choice, which is 🔴 and yours.
  On a 6 GB RTX 4050 that realistically means a quantised 7–8B class model; I will not pick it.

### 6. WP2-15 — real GPS/GEO windows
The GEO windows are still placeholders. Analytic transfer and plane-change burns, labelled as
approximations.

### 7. C05 weather — decide, do not leave it drifting
There is no weather data source, so C05 will report "not evaluated" forever. Either wire a real
cited source, or state in the contract that it is out of scope for simulated missions. Right now it
reads as an oversight rather than a decision.

### Not in scope for "the backend is complete"
WP2-13 fine-tuning and WP2-14 RL are Tier C in the handoff and both need a GPU and the benchmark
before they mean anything. Say the word if you want them; otherwise the backend is complete without
them and they stay on the list.

---

## The order I would build it in

This is your own ordering from the optimizer task, with the two correctness fixes moved in front
because everything downstream records numbers that depend on them.

1. Verifier `evaluated` boolean  *(item 1 — small, unblocks two lanes)*
2. Split ascent vs on-orbit propellant  *(item 2 — makes fuel optimisable at all)*
3. Per-vehicle tolerances  *(item 3b — before anything is frozen)*
4. WP2-12 benchmark suite, split proposed for your approval  *(item 3)*
5. WP2-10 memory, measured against that benchmark  *(item 4)*
6. WP2-11 `OptimizerAgent` and the tool layer  *(item 5, first half)*
7. WP2-15 GPS/GEO windows  *(item 6)*
8. The LLM planner, once you pick a model  *(item 5, second half — 🔴)*

Owner while Shiva is offline: claude-code, on Yash's instruction. His five directories
(`ascent/`, `env/`, `rendezvous/`, `optimize/`, `verifier/`) are normally his; every change I make
in them is listed here and in `status/shiva.md` so he can see exactly what moved.
