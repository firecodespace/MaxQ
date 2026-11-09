# MaxQ — Project Handoff (v2: tool-agnostic, lead-governed)

> **From the live satellite catalog to orbit.**
> Overnight sprint: 2026-11-10, 00:30 → 07:30 IST.
> **Project Lead: Yash.** Lane owner: **Shiva.** Any AI agent (Claude Code, ChatGPT/Codex, Antigravity, Gemini, Cursor, …) works on behalf of one of them.
> Read `AGENTS.md` first. This file is the project brief: scope, interfaces, work packages, templates.

---

## 0. Reading order and how to use these files

| File | Purpose | Who reads it |
|---|---|---|
| `AGENTS.md` | Universal charter: authority, freedom levels, quality bar, git etiquette | Every agent, every session |
| `HANDOFF.md` (this file) | Project brief: scope, contracts, work packages, templates | Every agent, relevant sections |
| `status/BOARD.md` | Lead's dashboard of work packages | Yash writes; everyone reads |
| `status/<name>.md` | Each owner's running status and handoff notes | The owner (and their agents) write |
| `docs/decisions.md` | Decision log | Anyone appends; Yash resolves |
| `docs/proposals/` | Short proposals for Yellow/Red changes | Anyone writes; Yash decides |

**Tool setup (one-time, 2 minutes)**
- Create `CLAUDE.md`, `GEMINI.md` and any other tool-specific file with one line: *"Read and follow `AGENTS.md`, then `HANDOFF.md`."* Many agent tools also read `AGENTS.md` directly; check each tool's docs.
- For chat-only tools (e.g. ChatGPT in a browser), build a **context pack**: `AGENTS.md` + §5 (contracts) + the WP brief + any files to change. See `AGENTS.md` §7 (paste mode).
- Never put API keys or personal emails in any of these files.

---

## 1. Governance

### 1.1 Roles

| Person | Role | Lane (default folders) | Default work packages |
|---|---|---|---|
| **Yash** | **Project Lead** + lane owner | `catalog/`, `mission/`, `web/`, `scripts/`, `api/`, `core/frames.py` | WP-00, 01, 02, 03, 04, 08, 09, 12 |
| **Shiva** | Lane owner | `ascent/`, `manifest/` | WP-00 (shared), 05, 06, 07, 10, 11 |
| **AI agent** | Acts for a human | The invoking human's lane | As assigned by that human |

Interests: Yash — AI, ML, big data, space. Shiva — AI, data science, quantum. Work packages are assigned to match, but **Yash may reassign any WP** to any person or agent at any time.

### 1.2 Decision rights (summary of `AGENTS.md` §3)

- 🟢 **Green (just do it):** anything inside your lane that keeps contracts and tests intact.
- 🟡 **Yellow (do it, then tell the lead):** new pinned dependency, additive optional contract fields, small shared-helper changes, scope trims inside a WP, reordering inside a tier.
- 🔴 **Red (Yash's OK first):** breaking contract changes, tier changes, editing another lane, deploy settings, secrets handling, `main` rules, rewriting history, deleting data or branches.

### 1.3 Lead operating cadence (Yash)

| Time (IST) | Checkpoint | Yash does |
|---|---|---|
| 01:00 | Kickoff check | Confirm contracts + dummy data landed; update `status/BOARD.md` |
| **03:00** | **Integration sync** | Real data flows both ways; resolve contract mismatches; approve gated merges |
| **04:30** | **v1 go/no-go** | Decide deploy; if P0 slipped, decide cuts |
| **06:30** | **Feature freeze** | Declare freeze; only fixes and polish after this |
| 07:30 | Close | Final merge, deploy, README sign-off |

Between checkpoints, agents and owners run autonomously within their lane. Escalate only Red items and 20-minute blockers.

### 1.4 When the lead is away

Pick the most reversible reasonable option, log it in `docs/decisions.md` as `PROVISIONAL`, and continue. Yash reviews provisional decisions at the next checkpoint and confirms or reverses them. Stop and wait only for Red items that can't be undone.

---

## 2. Operating model for mixed humans and agents

- **Lanes, not micromanagement.** Each lane owner defines how to build their deliverables. Interfaces (contracts) and acceptance tests are the only hard constraints.
- **One writer per file.** Parallel agents under the same owner use separate branches/worktrees and separate files.
- **Agent identity.** Commits carry `Agent:`, `On-behalf-of:`, `WP:` trailers (see `AGENTS.md` §6) so Yash can see which tool produced what.
- **Merge policy.** Lane merges: owner merges when `make test` passes. Gated merges (contracts, `core/`, shared config, deploy, README structure): Yash approves.
- **Interface change protocol.**
  1. Prefer **additive** changes: new optional fields, bump nothing. (🟡)
  2. For a breaking change, write `docs/proposals/NNN-title.md` (problem, proposal, who is affected, rollback), and wait for Yash. (🔴)
  3. On approval: update the schema and `data/dummy/`, bump `schema_version`, notify consumers in `status/BOARD.md`.
- **Delegation to other agents.** Fill in the **Work Order** template (§10.1) and give it, together with `AGENTS.md`, to any agent. The agent returns work in the Reporting format.
- **Session continuity.** Each agent ends with a **Handoff Note** (§10.2) so another tool or person can resume.

---

## 3. Project summary

**MaxQ** is a mission planner for rocket launches built on live orbital data.

| Profile | Destination | Key constraints |
|---|---|---|
| `crew_iss` | ISS (≈51.6° inclination, ≈400–420 km, read from live TLE) | Instantaneous window, strictest weather, fast rendezvous preferred, abort considerations |
| `cargo_iss` | ISS | Same orbit, looser weather, heavier payload, slower rendezvous allowed |
| `geo_comm` / `meo_gps` | GEO (35,786 km altitude, ~0° inclination) for communications; MEO (≈20,200 km, ≈55°) for GPS | Multi-burn upper stage, coast phases, plane-change cost, site latitude matters |

**Pipeline:** live catalog → real launch windows and azimuth → optimized ascent → collision screening → mission scheduling → browser demo.

**Non-goals:** flight-grade accuracy, real engine curves, Mach-dependent drag, winds, flight-software guidance, RL guidance (a follow-up project), exact real-rocket specs.

### Scope tiers (Yash owns changes to these)

| Tier | Deadline | Contents |
|---|---|---|
| **P0** | v1 deployed by **04:30** | WP-00…05, 03, 04: data, windows, basic ascent sim, globe with live satellites, replay, deploy |
| **P1** | Freeze **06:30** | WP-06…10, 09: optimizer, rocket design, screening, clustering, scheduler, three profiles |
| **P2** | If ahead | WP-11 (QAOA), WP-13+ (LLM interface, warm-start ML, SGP4 residual, space-weather overlay) |

Do not start a lower tier while a higher-tier item is broken.

---

## 4. Architecture

```
 CelesTrak ─► catalog/fetch ─► catalog.parquet ─┬─► mission/windows ─► windows.json ─┐
                                                 └─► catalog/cluster ─► clusters.json │
                                                                                      ▼
                         rocket.json ─► ascent/sim (+optimize, design) ─► trajectory.json
                                                                           │        │
                                            catalog/screen ◄───────────────┘        │
                                                 │ risk.json                        │
                                                 ▼                                  ▼
                                   manifest/ (instances, solvers, qaoa) ─► result.json, bench.json
                                                 │
                       scripts/export_web_data ──┴─► web/public/data/*.json ─► static site
```

Python produces JSON offline. The site is static; live satellite motion runs in the browser (`satellite.js`). Optional P2: a serverless LLM proxy.

**Shared decisions (don't relitigate overnight):** SI units; UTC unix seconds; SGP4 output is TEME; trajectories export as geodetic `lat_deg/lon_deg/alt_m`; ascent sim is a 2D point mass in the launch plane mapped to lat/lon by azimuth; post-parking-orbit maneuvers are analytic impulsive burns; rockets are fictional archetypes; seed all randomness.

---

## 5. Data contracts (the hard interface)

Schemas live in `contracts/*.schema.json`; `contracts/validate.py` exposes `validate(kind, obj)`. Every file has `"schema_version": 1`. Every contract has a `data/dummy/<name>_dummy.json` by 01:00.

### 5.1 `catalog.parquet` — producer: WP-01
Columns: `norad_id:int`, `name:str`, `group:str` (`stations|starlink|gps-ops|geo|debris|active|…`), `tle1`, `tle2`, `epoch_utc` (ISO-8601), `incl_deg`, `ecc`, `mean_motion_rev_day`, `raan_deg`, `alt_km_approx`.
**Additive extension (2026-11-10, schema_version still 1):** `tle1`/`tle2` are null only for `norad_id >= 100000` (no TLE exists); optional OMM columns `intl_designator, classification, ephemeris_type, elset_no, rev_at_epoch, argp_deg, mean_anomaly_deg, bstar, mean_motion_dot, mean_motion_ddot` carry the full SGP4 element set (propagate with `catalog.omm.satrec_from_row`, never assume a TLE exists).

### 5.2 `windows.json` — producer: WP-02 → consumers: WP-05, 08, 10, web
```json
{
  "schema_version": 1, "profile": "crew_iss",
  "site": {"name": "Cape Canaveral", "lat_deg": 28.5, "lon_deg": -80.6},
  "target": {"norad_id": 25544, "tle_epoch_utc": "2026-11-10T00:00:00Z"},
  "generated_utc": "2026-11-10T01:00:00Z",
  "windows": [{
    "id": "w0", "t_launch_unix": 1794286000, "t_launch_utc": "2026-11-10T04:46:40Z",
    "azimuth_deg": 44.9, "plane_error_deg": 0.0, "pass": "ascending",
    "phase_angle_deg": 31.2, "rendezvous_hours_fast": 3.2, "rendezvous_hours_slow": 25.0,
    "flyable": true
  }]
}
```

### 5.3 `rocket.json` — producer: WP-05/07
```json
{
  "schema_version": 1, "id": "medlift_kerolox_a", "name": "MedLift-A (fictional archetype)",
  "profile": "cargo_iss", "payload_kg": 8000,
  "stages": [
    {"dry_kg": 22000, "prop_kg": 400000, "thrust_n": 7600000, "isp_s": 282, "cd": 0.3, "area_m2": 10.8},
    {"dry_kg": 4000,  "prop_kg": 110000, "thrust_n": 934000,  "isp_s": 348, "cd": 0.3, "area_m2": 10.8}
  ]
}
```
Numbers are placeholders showing shape; tune in `ascent/rockets.py`.

### 5.4 `trajectory.json` — producer: WP-05/06 → consumers: WP-03, 08, web
```json
{
  "schema_version": 1, "id": "traj_medlift_a_w0", "rocket_id": "medlift_kerolox_a",
  "window_id": "w0", "t0_unix": 1794286000, "seed": 42,
  "t_s": [0, 1, 2], "lat_deg": [28.5, 28.5, 28.5], "lon_deg": [-80.6, -80.6, -80.6],
  "alt_m": [0, 3, 12], "speed_mps": [408, 411, 420], "q_pa": [0, 20, 90], "mass_kg": [545000, 543900, 542800],
  "events": [{"t_s": 70, "name": "max_q"}, {"t_s": 160, "name": "meco"}, {"t_s": 165, "name": "stage_sep"}, {"t_s": 520, "name": "insertion"}],
  "final_orbit": {"perigee_km": 200, "apogee_km": 205, "incl_deg": 51.6, "ecc": 0.0004},
  "params": {"pitch_program": [0.0, 0.0]}
}
```
1 Hz sampling; all arrays equal length.

### 5.5 `risk.json` — producer: WP-08
```json
{
  "schema_version": 1, "threshold_km": 25,
  "results": [{
    "window_id": "w0", "trajectory_id": "traj_medlift_a_w0", "min_miss_km": 41.3,
    "object": {"norad_id": 12345, "name": "EXAMPLE DEB"}, "t_closest_utc": "2026-11-10T04:52:10Z",
    "risk_score": 0.12, "status": "go"
  }]
}
```
`status` ∈ `go | review | no-go`. Thresholds are configurable and illustrative; real launch collision avoidance uses larger, agency-specific keep-out volumes.

### 5.6 `manifest_instance.json`, `result.json` — producer: WP-10
```json
{"schema_version": 1, "id": "manifest_12_seed1", "pads": ["LC-A", "LC-B"], "missions": ["m0", "m1"],
 "tasks": [{"id": 0, "mission": "m0", "pad": "LC-A", "start_unix": 1794286000, "end_unix": 1794286600, "value": 7.5}],
 "conflicts": [[0, 5], [0, 9]]}
```
```json
{"instance": "manifest_12_seed1", "solver": "sa", "selected": [0, 4, 9], "score": 123.4, "runtime_s": 0.8, "seed": 1}
```
Conflicts: same pad overlapping (plus turnaround), same ISS docking port overlapping, windows with `no-go` risk. QUBO: minimize `-Σ vᵢxᵢ + P·Σ xᵢxⱼ` over conflicting pairs, `P > max vᵢ`.

### 5.7 `clusters.json` — producer: WP-09
`{"schema_version":1,"features":["incl_deg","log_mean_motion","ecc"],"clusters":[{"label":0,"name":"…","count":5400,"centroid":{}}],"points":[{"norad_id":0,"cluster":0,"x":0.0,"y":0.0}]}` where `x,y` is the 2D embedding for display.

---

## 6. Work packages (outcome-based)

Each WP states the **outcome**, what is **fixed** (must), and what is **free** (your call). Time boxes are guidance; the acceptance tests are the finish line. Default owners can be reassigned by Yash.

### WP-00 · Kickoff and scaffolding — shared · 00:30–01:00 · P0
- **Outcome:** a repo where every lane can start without waiting.
- **Deliverables:** folders per §9; `.gitignore`; `Makefile` (`setup`, `test`, `data`, `windows`, `ascent`, `manifest`, `web`); pinned `requirements.txt`; `contracts/*.schema.json` + `validate.py`; `data/dummy/*_dummy.json` for every contract; `status/BOARD.md`, `status/yash.md`, `status/shiva.md`, `docs/decisions.md`; one-line `CLAUDE.md`/`GEMINI.md` pointers.
- **Must:** dummy files validate; `make test` runs.
- **Free:** schema library (`jsonschema` or `pydantic`), Makefile details.
- **Split:** Yash — catalog/windows/risk/clusters dummies, schemas, Makefile. Shiva — rocket/trajectory/manifest dummies, environment.

### WP-01 · Catalog pipeline and frames — Yash · 01:00–01:45 · P0
- **Outcome:** the live catalog and ISS orbit are loadable, cached, and propagate correctly.
- **Deliverables:** `catalog/fetch.py`, `data/processed/catalog.parquet`, `core/frames.py` (GMST, TEME↔ECEF/ECI, geodetic↔ECEF), tests.
- **Must:** cache raw fetches; offline sample works; ISS NORAD 25544 propagates via SGP4; geodetic→ECEF→geodetic error < 1 m.
- **Free:** pandas vs polars; group selection; verify CelesTrak endpoints and rate limits in their docs.

### WP-02 · Launch windows and phasing — Yash · 01:45–03:00 · P0
- **Outcome:** for a site and profile, a list of real launch windows with azimuth and rendezvous estimates.
- **Deliverables:** `mission/{sites,windows,phasing,profiles}.py`, `windows.json`, tests.
- **Must:** sites include Cape Canaveral (≈28.5°N) and Kourou (≈5.2°N); known-value tests in §7 pass; descending-pass solutions are `flyable: false` by default (configurable per site).
- **Free:** root-finding method. Suggested robust approach: propagate the target with SGP4, `h = r × v`, site ECI position from GMST; the plane passes over the site when `h · r_site(t) = 0`; scan, bracket sign changes, refine with `brentq`. Phasing can be a simple impulsive phasing-orbit estimate.

### WP-03 · Frontend v0 — Yash · 01:30–04:00 · P0
- **Outcome:** a browser page with moving live satellites, a window panel, and trajectory replay.
- **Deliverables:** `web/` with globe or 2D map (`satellite.js` for SGP4 in a Web Worker), window panel from `windows.json`, replay scrubber with event markers and telemetry plots (altitude, speed, q).
- **Must:** works on dummy data first; swap to real at the 03:00 sync; graceful when a data file is missing; dummy data visibly labeled.
- **Free:** three.js vs `globe.gl` vs 2D canvas, UI design, chart library. If time is short, ship 2D map plus plots.

### WP-04 · Deploy v1 — Yash · 04:00–04:30 · P0
- **Outcome:** a public URL.
- **Must:** clean browser profile and mobile width verified, no console errors, URL recorded in `status/yash.md`. (Deploy settings are 🔴 if they affect other people's accounts or domains.)
- **Free:** host (Vercel / GitHub Pages).

### WP-05 · Ascent simulator — Shiva · 01:30–03:00 · P0
- **Outcome:** a simulator that flies a rocket archetype to a parking orbit and exports `trajectory.json`.
- **Deliverables:** `ascent/{rockets,atmosphere,geo,sim}.py`, 3 fictional archetypes (crew launcher, cargo launcher, heavy launcher for GEO/MEO), tests.
- **Must:** 2D point mass with thrust/Isp, staging, inverse-square gravity, drag `½ρv²·Cd·A`, Earth-rotation boost `ω·R·cosφ`, parameterized pitch program, `max_q` and stage events, 1 Hz export in the contract shape; sanity checks in §7 pass.
- **Free:** integrator (RK4 or `solve_ivp` with events), pitch-program parameterization, atmosphere model (suggested: exponential, ρ₀ = 1.225 kg/m³, H ≈ 8.5 km). Ship a hand-tuned baseline first; WP-06 improves it.

### WP-06 · Pitch/staging optimizer — Shiva · 03:00–04:30 · P1
- **Outcome:** an optimized ascent that beats the baseline.
- **Must:** reaches the target orbit; seed recorded; improvement reported as measured with a naive-vs-optimized plot.
- **Free:** CMA-ES, Bayesian optimization, evolutionary methods, parameterization, constraint handling.

### WP-07 · Rocket design search — Shiva · 04:30–05:30 · P1
- **Outcome:** a Pareto front of designs per mission profile.
- **Must:** objectives include payload to target orbit (max) and a size or mass measure (min); finalists verified with the sim; results saved in `data/results/`.
- **Free:** NSGA-II (`pymoo`) or any multi-objective method; objective set beyond the two above.

### WP-08 · Collision screening — Yash · 04:30–05:30 · P1
- **Outcome:** each window gets `go / review / no-go` from catalog conjunction screening.
- **Deliverables:** `catalog/screen.py`, `risk.json`, tests.
- **Must:** vectorized SGP4 (`SatrecArray`/`sgp4_array`); budget about 30k objects × 2 h at ≤10 s steps in roughly 60 s (use pre-filtering by altitude band and chunking); synthetic test places an object near the path and it is flagged; TLE accuracy caveat in metadata.
- **Free:** pre-filter strategy, risk-score formula, threshold defaults.

### WP-09 · Catalog clustering and congestion — Yash · 05:30–06:30 · P1
- **Outcome:** a Sky view showing constellations, the GEO belt, debris clouds, and congested shells.
- **Must:** `clusters.json` validates; clusters visibly separate known groups.
- **Free:** features, DBSCAN vs other methods, UMAP vs PCA, heatmap design.

### WP-10 · Manifest scheduler — Shiva · 05:30–06:30 · P1
- **Outcome:** a comparison of scheduling methods on launch manifests.
- **Must:** instances built from `windows.json` (+ `risk.json` when available) at about 12, 50, 200, 1000 tasks; greedy, simulated annealing, and CP-SAT as the exact reference; a quality-vs-runtime table and plot; one command reproduces (`make manifest`).
- **Free:** solver internals, instance generator details, plotting.

### WP-11 · QAOA benchmark — Shiva · 06:30–07:30 · P2
- **Outcome:** an honest quantum-vs-classical result on small instances.
- **Must:** ≤ ~12 qubits, depth p = 1–3, simulator only; approximation ratio vs CP-SAT optimum; one plot and one honest paragraph (failure modes included).
- **Free:** PennyLane vs Qiskit Aer, ansatz, optimizer.

### WP-12 · Profiles UI, README, demo — Yash · 06:30–07:30 · P1
- **Outcome:** a polished, documented demo.
- **Deliverables:** mission-profile selector, Risk and Manifest tabs, README (architecture, fidelity statement, results), 60-second demo GIF.

### P2 work packages (assign if ahead)

| WP | Item | Default owner | Notes |
|---|---|---|---|
| WP-13 | LLM mission interface | Yash | Natural-language request → mission parameters; explains why a window closed. Serverless proxy; key in env only. |
| WP-14 | Scheduler warm-start model | Shiva | Gradient boosting predicts which tasks are in the optimal set (labels from CP-SAT); seeds SA; report the gain honestly. |
| WP-15 | SGP4 residual correction | First one free | Needs historical TLEs (free Space-Track account). |
| WP-16 | Space-weather overlay | Yash | NOAA Kp index as context; display only. |

---

## 7. Physics reference and acceptance tests

**Constants** (in `core/constants.py`, with sources): μ = 3.986004418×10¹⁴ m³/s²; R_E = 6,378.137 km; ω_E = 7.2921159×10⁻⁵ rad/s; g₀ = 9.80665 m/s².

**Formulas:** circular speed `v = √(μ/r)`; rotation speed `ω_E R_E cos φ`; minimum direct-launch inclination = |latitude|; inertial azimuth `sin β = cos i / cos φ`; rotating-frame azimuth `β_rot = atan2(v_orb sin β − ω_E R_E cos φ, v_orb cos β)`; rocket equation `Δv = Isp g₀ ln(m₀/m_f)`; dynamic pressure `q = ½ρv²`; exponential atmosphere `ρ = ρ₀ e^(−h/H)`; plane change `Δv = 2v sin(Δi/2)`.
**Reference orbits:** GEO radius 42,164 km (35,786 km altitude); GPS (MEO) ≈20,200 km altitude, ≈55° inclination, period ≈11 h 58 min; ISS NORAD 25544 — read inclination and altitude from the live TLE, never hard-code.

**Known-value tests (must pass)**

| Test | Expected |
|---|---|
| `test_min_inclination` | equals site latitude for several sites |
| `test_azimuth_cape_iss` | Cape 28.5°N, i = 51.64° → azimuth ≈ 44.9° (accept 44–46°) |
| `test_rotation_boost_cape` | ≈ 408 m/s (accept 405–412) |
| `test_circular_speed_200km` | ≈ 7.78 km/s (± 0.02) |
| `test_frames_roundtrip` | error < 1 m |
| `test_windows_per_day` | about 2 plane crossings per day per site for the ISS plane |
| `test_sim_reaches_orbit` | perigee ≥ ~160 km, ≈ 7.8 km/s at insertion, small eccentricity |
| `test_max_q_plausible` | roughly 60–90 s (flag if outside 40–120 s) |
| `test_losses_plausible` | gravity + drag losses typically 1.3–2 km/s |
| `test_screen_flags_synthetic` | an object near the path is flagged |
| `test_contracts_validate` | every file in `data/dummy/` and `data/results/` validates |

**Informational check (not pass/fail):** compare computed windows with upcoming ISS-bound launches from Launch Library 2 (cache; mind rate limits). Differences are expected because real windows include operational constraints we don't model.

---

## 8. Timeline (IST)

| Time | Milestone |
|---|---|
| 00:30–01:00 | WP-00 kickoff |
| 01:00–03:00 | Parallel: WP-01 → 02 (+03), WP-05 |
| **03:00** | **Integration sync** (lead checkpoint) |
| 03:00–04:30 | WP-03/04 finish and deploy; WP-06 |
| **04:30** | **v1 live — go/no-go** |
| 04:30–06:30 | P1: WP-08, 09; WP-07, 10 |
| **06:30** | **Feature freeze** |
| 06:30–07:30 | WP-12 polish; WP-11 if time; README; demo GIF |
| 07:30 | Final push; stop and sleep |

Eat and take a break around 03:00. Hard stop at 07:30: tired commits at 06:00 are how repos break.

**Integration dependencies**

| Consumer | Needs | From | Dummy available | Real by |
|---|---|---|---|---|
| WP-05 | `windows.json` (site, azimuth, inclination) | WP-02 | 01:00 | ~03:00 |
| WP-03 | `trajectory.json` | WP-05 | 01:00 | ~03:00 |
| WP-08 | `trajectory.json` | WP-05/06 | 01:00 | ~03:00 baseline, ~04:30 optimized |
| WP-10 | `windows.json`, `risk.json` | WP-02, 08 | 01:00 | ~03:00 / ~05:30 |

---

## 9. Repo layout and standards

```
maxq/
├─ AGENTS.md  HANDOFF.md  CLAUDE.md  GEMINI.md  README.md  Makefile  requirements.txt  .gitignore
├─ contracts/        # schemas + validate.py                          (gated)
├─ core/             # constants.py, units.py (gated); frames.py (Yash)
├─ data/             # raw/ (ignored), processed/, dummy/, results/
├─ catalog/          # Yash: fetch, cluster, screen
├─ mission/          # Yash: sites, windows, phasing, profiles
├─ ascent/           # Shiva: rockets, atmosphere, geo, sim, optimize, design
├─ manifest/         # Shiva: build_instances, solvers/{greedy,sa,cpsat,qaoa}, bench
├─ tests/            # mirrors package layout
├─ scripts/          # Yash: export_web_data.py
├─ web/              # Yash: static site, public/data/
├─ api/              # Yash (P2): LLM proxy
├─ status/           # BOARD.md (Yash), yash.md, shiva.md
└─ docs/             # decisions.md, proposals/
```

**Stack (defaults, Green to change inside your lane if contracts and tests hold):** Python 3.11+, `sgp4`, `numpy`, `scipy`, `pandas`/`polars`, `pyarrow`, `scikit-learn`, `umap-learn` (optional), `cma`, `pymoo`, `ortools`, `pennylane` or `qiskit-aer`, `pytest`, `ruff`, `jsonschema`/`pydantic`. Frontend: ES modules, `satellite.js`, `three.js`/`globe.gl`, no build step unless it clearly saves time.

**Data sources:** CelesTrak GP/TLE (catalog, ISS, GPS, GEO, debris; verify endpoints and rate limits in their docs); Launch Library 2 (sanity check; rate-limited; cache); Open-Meteo (launch-site weather, free, no key); NOAA SWPC (P2); Space-Track (P2, free account).

**Quality bar:** `make test` green on `main`; contracts validated at write and read; seeds recorded; offline mode works; no secrets or large raw data in git; dummy data labeled.

---

## 10. Templates

### 10.1 Work Order (give to any agent, with `AGENTS.md`)

```markdown
# Work Order — <WP-xx>: <title>
Requested by: <Yash|Shiva>        Agent/tool: <name>        Time box: <HH:MM–HH:MM>
## Outcome
<one or two sentences: what must be true when you're done>
## Context to read
- AGENTS.md, HANDOFF.md §<sections>, contract(s): <files>, existing code: <paths>
## Inputs / Outputs
- Consumes: <file/contract>      Produces: <file/contract>
## Hard constraints (must)
- <contracts, acceptance tests, units, deadlines>
## Freedom (your call)
- <algorithms, libraries, structure, naming>
## Acceptance tests
- <tests from HANDOFF §7 or the WP>
## Report back with
- Done / Files changed / How to run / Contract changes / Next-Blocked-Needs
```

### 10.2 Session Handoff Note (write into `status/<name>.md` when stopping)

```markdown
## Handoff — <name> / <agent+tool> — HH:MM
- WP: <id> — state: <done | partial | blocked>
- Done: <what works, with the command/output that proves it>
- Half-done: <what exists but isn't finished; where it lives>
- Next steps: <ordered, concrete>
- Traps found: <things that cost time>
- Resume with: <exact commands>
- Contract changes: <none | details>
```

### 10.3 Proposal (`docs/proposals/NNN-title.md`) — for 🟡/🔴 changes

```markdown
# Proposal NNN — <title>
Author: <name/agent>     Level: <Yellow|Red>     Status: <proposed|approved|rejected>
- Problem: <2–3 lines>
- Proposal: <2–5 lines>
- Who is affected: <WPs/lanes>
- Trade-offs / alternatives: <short>
- Rollback: <how to undo>
```

### 10.4 Decision log entry (`docs/decisions.md`)

```markdown
- HH:MM — <decision> — by <name/agent> — rationale: <short> — reversible: <yes|no> — status: <FINAL|PROVISIONAL>
```

### 10.5 Board (`status/BOARD.md`, maintained by Yash)

```markdown
| WP | Title | Owner | Agent/tool | Tier | State | Blocker |
|----|-------|-------|------------|------|-------|---------|
| 01 | Catalog pipeline | Yash | claude-code | P0 | in progress | — |
```

---

## 11. Risks and fallbacks

| Risk | Fallback |
|---|---|
| CelesTrak slow/rate-limited | Use the cached snapshot; commit a small sample |
| Ascent sim won't reach orbit | Ship the hand-tuned baseline; coarse pitch program; widen tolerances; optimize afterwards |
| Window numerics off | Check against closed-form azimuth and the known-value tests; verify TEME/GMST handling |
| Screening too slow | Pre-filter by altitude band; coarser step; chunked vectorization; fewer objects |
| Frontend behind | Ship 2D map + plots without the 3D globe |
| QAOA messy | ≤12 qubits, p ≤ 2; report as measured |
| Tools disagree on style/structure | Contracts and tests win; style is Green inside a lane; Yash arbitrates |
| Merge conflicts | One writer per file; stay in lanes; shared edits are small and announced |
| Fatigue | Hard stop 07:30; no new features after 06:30 |

---

## 12. Fidelity statement (copy into README)

MaxQ combines real orbital data with reduced-fidelity models. **Real:** the live catalog and ISS orbit from public TLEs; orbital-mechanics math for launch windows, azimuth, and phasing. **Approximate:** the ascent model is a 2D point mass with a simple atmosphere; rockets are fictional archetypes; TLE positions carry kilometer-level uncertainty; collision-screening thresholds are illustrative. **Simulated:** QAOA runs on a classical simulator at small qubit counts and makes no claim of quantum advantage. MaxQ is an educational and portfolio project, not an operational planning tool.

---

## 13. Definition of done and resume bullets

- [ ] Public repo and live URL
- [ ] README: architecture, fidelity statement, results table and plots, demo GIF
- [ ] One command per stage (`make data|windows|ascent|manifest|web`)
- [ ] Tests green on `main`; contracts validate
- [ ] `status/BOARD.md` and both status files current; `docs/decisions.md` has no unresolved `PROVISIONAL` items

**Resume bullet templates (fill with measured numbers only):**
- Built MaxQ, a launch mission planner computing real ISS launch windows and azimuths from live CelesTrak data for [N] objects, with phasing estimates and collision screening of [M] ascent trajectories against the catalog.
- Optimized rocket ascent trajectories with CMA-ES, cutting propellant use by [X%] versus a hand-tuned baseline; searched designs with NSGA-II to produce Pareto fronts for [K] mission profiles.
- Formulated multi-mission launch scheduling as a QUBO and benchmarked greedy, simulated annealing, CP-SAT and QAOA: SA reached [X%] of optimal at [Y×] lower runtime on [Z]-task instances.
- Shipped an interactive browser globe with [N] live satellites, ascent replay, and risk views, deployed at [URL].

---

## 14. First-30-minutes checklist

- [ ] Repo created; Yash and Shiva have push access
- [ ] `AGENTS.md`, `HANDOFF.md`, and one-line `CLAUDE.md`/`GEMINI.md` pointers committed
- [ ] `contracts/` schemas + `validate.py` committed
- [ ] `data/dummy/*_dummy.json` for every contract committed
- [ ] `Makefile`, `requirements.txt`, `.gitignore` committed
- [ ] `status/BOARD.md`, `status/yash.md`, `status/shiva.md`, `docs/decisions.md` created
- [ ] Each person's environment runs `make test`
- [ ] Every agent session starts with: *"I'm `<name>`, working on `<WP-xx>`"*
