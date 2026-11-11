# status/antigravity.md — tasks for Yash's Antigravity agent

Read `AGENTS.md` first, then the parts of `HANDOFF_PHASE2.md` your task names.

**Who you are.** You act on behalf of Yash and inherit his lane and authority, never more
(`AGENTS.md` §2). Yash's lane is `web/`, `scripts/`, `server/`, `tests/`, `contracts/`, `notify/`,
`docs/`, `status/`.

**Whose files you must not touch.** `ascent/`, `env/`, `rendezvous/`, `optimize/`, `verifier/` are
Shiva's and he is actively editing all five right now. One writer per file (`AGENTS.md` §5). Read
them freely; change nothing in them. If a task looks like it needs a change there, stop and write
the finding into this file instead — that is a result, not a failure.

**Also being edited right now, by claude-code:** `tests/scripts/test_rocket_library.py`,
`notify/`, `web/js/main.js`. Leave those alone too.

**Branch.** `yash/antigravity-<task-slug>`, cut from
`claude/maxq-wp00-contracts-scaffold-d11107` (not `main` — main is several commits behind and does
not have the wizard, the CMA-ES rename, the charter additions or the notifier).

**The bar.** Every task below says what "done" means and how to prove it. "Done" means you ran that
command and saw it pass, and you report the actual output (`AGENTS.md` §5). Report in the §9 format
into this file. If you are stuck for 20 minutes, stop and write up what you tried, the blocker, two
options and your recommendation.

**The honesty rules are not optional** (`AGENTS.md` §11, items 1–12). Two that will bite on these
tasks specifically: a metric with no real source is `null`, never `0`; and never present a
simulated or assumed number as a measured or cited one.

---

## A1 — Validate the time-to-dock figure against a real mission  *(P0, no code changes)*

**Why.** The simulator currently reports 48,890 s — 13.6 hours — from liftoff to dock for a Falcon 9
cargo run to the ISS. Real Dragon profiles are roughly 6 hours, sometimes about 3. If our figure is
wrong by a factor of two, every "minimum time" result built on it is wrong by the same factor, and
the WP2-12 benchmark would enshrine it.

**Do.** Pick three real ISS missions with published liftoff and docking times (Crew Dragon, Cargo
Dragon, Cygnus or Progress are all fine — different profiles are *more* useful than three of the
same). For each, record liftoff UTC, docking/berthing UTC, the elapsed time, the number of phasing
orbits if stated, and the source URL. Cite real pages, fetched and read, not recalled
(`AGENTS.md` §11 item 6).

**Deliver.** `data/reference/iss_rendezvous_profiles.json` — a list of objects with
`mission`, `vehicle`, `liftoff_utc`, `dock_utc`, `elapsed_s`, `phasing_revs` (null if not stated),
`source_url`, `retrieved_utc`, `confidence`. Plus a short section in this file: how our 48,890 s
compares, and whether the gap is explained by profile choice or is a modelling error.

**Done when.** The JSON exists with three cited missions and every `source_url` resolves, and your
write-up states plainly whether 13.6 hours is defensible.

**Do not.** Change anything in `env/` or `rendezvous/` to "fix" a gap you find. Report it.

---

## A2 — Mission control panels (WP2-08)  *(P1)*

**Why.** The run panel today shows four tiles and a table. A run now produces per-attempt
constraint reports, phase timings and an LVLH approach trajectory, and none of it is visible.

**Do.** A new module `web/js/mission.js`, rendering into the existing Mission view. Read
`web/js/api.js` for the endpoints and `web/js/wizard.js` for the house style.

Panels: attempt detail (which constraints passed, which failed, with the verifier's note);
phase timeline (ascent / parking / phasing / approach / dock with durations); the search trace
(generation, sigma, covariance condition — already in each `agent_step`'s `key_numbers`); and the
approach plot if `rel_pos_m` is present.

**Hard requirements.**
- Put the logic in `web/js/mission-logic.js` as pure functions, and test those. `web/js/mission.js`
  does DOM only. This is the pattern `wizard-logic.js` / `wizard.js` already follows, and the
  reason is that the honesty rules are testable in the logic layer and not in the DOM.
- A constraint the verifier could not evaluate must render as **"not evaluated"**, visibly distinct
  from "failed". They are different facts and the UI has to say so.
- No invented numbers. If a field is absent, show "n/a", never 0 and never a placeholder value.
- Do not edit `web/js/main.js` (claude-code is in it). Export an `initMission()` and leave a note
  here saying where it needs wiring in.

**Done when.** `node --test tests/web/mission.test.mjs` passes with at least 10 tests, including
one that a not-evaluated constraint is not rendered as a failure; `node --check web/js/mission.js`
is clean; and you have driven it against a real run on `http://localhost:8000` and say in your
report which mission id you used.

---

## A3 — A guard against comparing across simulator versions  *(P1)*

**Why.** Cost moved from 1.082 to ~1.24 when the approach and docking phases landed. That is
correct — a different simulator measuring a longer mission — but nothing in the codebase stops
someone plotting those two numbers on one axis and reporting a 15% regression. `AGENTS.md` §11
item 4 requires versioning; nothing currently enforces that versions are *respected*.

**Do.** A small module `scripts/compare_guard.py` with pure functions:
`comparable(a, b) -> bool` (same `sim_version` **and** same `verifier_version`), and
`assert_comparable(items)` raising a clear error naming the differing versions and the artefacts
they came from. Then use it: any place in `scripts/` or `tests/` that compares costs across
attempts should call it.

**Done when.** `pytest tests/scripts/test_compare_guard.py -q` passes, including a test that two
attempts from different `sim_version`s are refused, and one that the error message names both
versions. Report which existing call sites you changed.

**Do not.** Change `contracts/*.schema.json`. If you think a field is missing, say so here.

---

## A4 — A README that is true  *(P2, do last)*

**Why.** `README.md` predates Phase 2. Anyone arriving now — including a future agent — is told the
wrong thing about what this project is.

**Do.** Rewrite it to describe what actually exists today and what does not. It must state, in
plain words: the simulator's measured error against published payload figures; that there is no
best-known benchmark yet; that the search is CMA-ES over ascent plan parameters and is not an
agent and has no memory between runs; and that there is no training on historical launch data,
because no such dataset exists at the needed fidelity — the agent will learn from its own
simulated attempts.

**Done when.** Every claim in the README is one you can point at a file or a measurement for, and
you list those pointers in your report here. If you cannot source a claim, delete it.

---

## Your report goes below this line

<!-- Use the AGENTS.md §9 format: Done (with the command and its actual output) / Files changed /
     How to run / Contract changes / Next, Blocked, Needs from someone. -->
