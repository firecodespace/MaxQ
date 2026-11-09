# Review: Shiva's WP-10 (manifest scheduler), WP-11 (QAOA), WP-14 (warm start)

Reviewer: claude-code on behalf of Yash, 2026-11-10 ~04:15 IST. Scope: HANDOFF acceptance criteria and honesty of claims.
Evidence is reproducible: `python scripts/review_manifest_claims.py` (read-only). Nothing in `manifest/` or `ascent/` was changed.

## Verdict
The engineering is solid (contract-valid outputs, 15/15 manifest tests, exact CP-SAT reference, plots, `make manifest` wired).
The problems are about **what the results are allowed to claim**, not whether the code runs. Four items change what we can say in the
README and on a resume; fix those before the 06:30 freeze.

## Acceptance criteria
| WP | Criterion (HANDOFF) | Status |
|---|---|---|
| 10 | Instances from `windows.json` (+`risk.json`) at ~12/50/200/1000 tasks | PARTIAL: sizes correct, but only 147 tasks can come from real windows (see H3) |
| 10 | Greedy, SA, CP-SAT exact reference | OK (CP-SAT reports OPT on all four) |
| 10 | Quality-vs-runtime table and plot | OK (`manifest_benchmark.png`, bench table) |
| 10 | One command reproduces (`make manifest`) | PARTIAL: runs, but is not reproducible (see H4) |
| 11 | <= ~12 qubits, simulator only, ratio vs CP-SAT, one plot, honest paragraph with failure modes | OK |
| 11 | depth p = 1-3 | MISSING: only p = 1-2 are run (solver supports 3) |
| 14 | GB trained on CP-SAT labels, seeds SA, gain reported honestly | PARTIAL (see M3) |

## High: changes what we can claim
**H1. QAOA "100% of optimum" is not evidence the quantum circuit helps (WP-11).**
The returned schedule is the best *feasible* bitstring among 1,000 measurement shots, and feasibility is checked classically. At 12 qubits there
are 4,096 states, so 1,000 shots cover a large share of the space on their own. With no random-sampling control:
on the committed 12-task instance, best feasible of 1,000 *uniformly random* bitstrings reaches **90.6% of optimum on average and is exactly
optimal in 24% of trials**. QAOA p=1 scored 92.5% (about the random baseline); p=2 scored 100% (one instance, one seed).
The HONEST paragraph is also a hard-coded string whose numbers (93.0% at N=12) contradict the status note ("100% across all tested topologies") and
the exported result (100% at 12 tasks). Fix: add a random-shots baseline column, several seeds/instances, generate the paragraph from the run, and run
p=3 or say it was not run. Until then say "QAOA matched the optimum on small instances, comparable to random sampling with the same shots".

**H2. CP-SAT dominates; SA is not "faster" (WP-10).**
CP-SAT is exact *and* faster at every size (1,000 tasks: 0.12 s vs SA 0.68 s). SA scores below plain greedy at 200 and 1,000 tasks
(593.6 vs 599.4; 4,398.7 vs 4,582.3 against an optimum of 4,692.1: SA = 93.7%). The HANDOFF resume template "SA reached X% of optimal at Y x lower
runtime" is therefore false here. Honest wording: "CP-SAT solved 1,000-task instances to proven optimality in 0.12 s; SA reached 93.7% of optimal
and was slower". This is a result, not a defect, but the write-up must say it.

**H3. Instances are largely synthetic and not labelled (WP-10).**
`_note` says "Realistic launch manifest ...". Actual composition: 12 and 50 tasks use real window *times* only; 200 tasks = 147 real + 53 synthetic (26%);
1,000 tasks = 147 real + 853 synthetic (85%, random times and random risk status). Even for "real" tasks, mission and pad are drawn at random, not taken
from the window's profile (a `meo_gps` task can sit on a `crew_iss` window; Vandenberg pads are assigned to Cape windows). Scaling to 1,000 needs synthetic
fill, which is fine, but the instance `_note` and the README must state the real/synthetic split.

**H4. Not reproducible, and `manifest.bench` has no CLI (WP-10).**
`build_instance` reads whatever `windows_*.json` / `risk_*.json` are in `data/results` at run time. After my windows were regenerated, the same code and
seed produced a different 12-task instance (CP-SAT optimum 33.93 -> 31.83). Also, `python -m manifest.bench --help` ignores the flag and re-runs
everything, overwriting committed results (this happened during the review; files were restored from git). Fix: record the input window/risk files
(names + hash) in the instance, and add argparse so `--help` is harmless.

## Medium
**M1. Risk lookup keyed by `window_id` only.** Window ids (`w0`, `w2`, ...) repeat across profiles and across the baseline/optimized risk files, so risk from
cargo windows can be applied to crew/GPS/GEO windows and one risk file overwrites the other depending on glob order. Key by (profile, window_id). Yash side:
risk files will carry `metadata.windows_profile` so this join is possible (done in the same change as this review).

**M2. `no-go` windows are discounted (x0.3), not conflicts.** HANDOFF 5.6 lists "windows with `no-go` risk" among conflicts. Also an extra range-wide
"overlapping windows" conflict is used that the HANDOFF does not define; it is not stated in the instance `_note`.

**M3. WP-14 model quality is slightly overstated.** The held-out AUC uses a random row-level split of pooled tasks, not a split by instance (tasks of one
conflict graph land in both train and test). Re-done by instance (8 partitions): ROC-AUC **0.827** (0.796-0.850) vs shipped 0.860, and the trivial baseline
"rank by task value alone" already gets **0.775**, so the model adds about 0.05 AUC. The "3.1-4.9x speedup" is largely by construction (SA budget cut to 1/3;
timing excludes feature extraction and model inference), and CP-SAT solves these instances exactly in tens of milliseconds, so the warm start has no
operational use here. Report it as a measured, modest improvement and say so.

## Low
- Task `window_id` values (`w2_t5`, `syn_w7`) cannot be joined back to `windows.json`.

## What is good
Contract-valid everywhere (44/44), deterministic seeds, CP-SAT status reported, the SA benchmark in WP-14 uses an independent test instance, QAOA failure modes
are discussed qualitatively and correctly, and the instance builder really does read real windows and risk files when present.


---
# Round 2 (05:55 IST): regenerated artifacts verified; WP-15 reviewed

## Verified (re-run by the reviewer)
- All four committed manifest instances equal a fresh `build_instance(n, seed=42)`; every committed result is conflict-free and its score equals the sum of its selected values; fast tests 299 passed; contracts 44/44.
- `qaoa_benchmark.png` now has the `Random (1k shots)` control. Regenerated 12-task instance: CP-SAT optimum 48.91; QAOA p=1 96.2%, p=2 **80.5%**; uniform random best-of-1,000 mean **88.9%** (min 70.2%), exactly optimal in 20% of trials. So p=1 is above the random mean but inside the random distribution, and p=2 is worse than both.
- Solvers at 1,000 tasks: CP-SAT 4,204.9 (exact), greedy 97.2%, SA 95.3% (below greedy); at 200 tasks SA 95.8%, greedy 93.8%. Numbers for README/resume: use these.
- WP-14 (their pipeline): row-level ROC-AUC 0.883, instance-level 0.872 (0.845-0.892), rank-by-value baseline 0.800.

## Still wrong
**R2-1 (HIGH) WP-15 evidence is synthetic and undisclosed.** `data/sample/historical_tles_25544.json` is presented as a "historical 7-day TLE sequence". It is not: 15 snapshots exactly 12.00 h apart, RAAN falls by exactly 2.500 deg per step, argument of perigee rises by exactly 1.9 deg per step, mean motion and B* change linearly, eccentricity and inclination are identical in all 15 records. Real TLE histories are irregular and jitter in every element. Only the final record is real (it equals the ISS record in the live catalog). The "truth" used for scoring is another snapshot of the same synthetic sequence, and a quadratic + harmonic model is then fitted to it, so "103.8 -> 13.6 km, 86.9% reduction" measures recovery of the generator, not real SGP4 error. The commit message "tune residual regression to quadratic drift achieving 86.9% error reduction" shows the model was tuned to a target. Nothing in the file, the module docstring or the status note says the data is synthetic (AGENTS.md: mocks must be named `*_dummy.json` and labelled; never fake results).
Needed: either (A) relabel as synthetic (move to `data/dummy/`, `_note` "SYNTHETIC: linear drift generated for pipeline validation only", mark outputs synthetic, remove the 86.9% / 96.2% claims from status, BOARD, README), keeping the RIC code and tests as a method demo; or (B) real data: needs a free Space-Track account that Yash creates (agents cannot enter credentials), then re-run on real histories. Until then WP-15 is not a result and must not appear in the README or on a resume.

**R2-2 (HIGH) WP-11 generated paragraph contradicts the measured numbers.** It is only partly dynamic: it prints "QAOA p=2 reaches 80.5%, recovering the optimum through deeper entangling layers" and "p=2 visibly recovers approximation quality" unconditionally. Make the sentences conditional on the data (here p=2 is below p=1 and below the random baseline). The plot y-axis is cut at 60%, so p=2 at N=8-10 is off the chart; widen it.

**R2-3 (MED) tests overwrite committed results with local absolute paths.** `tests/catalog/test_residuals.py` runs `run_residuals_benchmark`, which rewrites `data/results/residuals/sgp4_residuals.json`, leaving a dirty tree and recording `plot_path` as a machine-specific absolute path (`C:\MaxQ\...`). Tests must write to `tmp_path`; store paths relative to the repo.

Info: Shiva edited one line of Yash's `tests/web/test_node_units.py` (loosened the "fail 0" string match, probably a Node reporter difference); harmless because the return code is also asserted.


## Round 2 follow-up (06:20 IST): WP-15 reworked
Yash asked for real data and for the problem to be fixed. The synthetic file and the results computed from it were deleted; `catalog/residuals.py` was rebuilt to evaluate only on a real Space-Track history (pairs of baseline/later snapshots, strict temporal split, manoeuvre exclusion, bootstrap CI, simpler baseline) and refuses non-real input; `scripts/fetch_spacetrack_history.py` fetches the history once with credentials from environment variables only. Real data could not be obtained by the agent (Space-Track needs an account; SatNOGS offers only the current element set; CelesTrak has no history). WP-15 therefore has no result yet.
