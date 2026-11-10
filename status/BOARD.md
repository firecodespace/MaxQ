# MaxQ Board (maintained by Yash)

Sprint: 2026-11-10, 00:30 -> 07:30 IST. Tiers: P0 deploy by 04:30, P1 freeze 06:30, P2 if ahead.

| WP | Title | Owner | Agent/tool | Tier | State | Blocker |
|----|-------|-------|------------|------|-------|---------|
| 00 | Kickoff and scaffolding | Yash + Shiva | claude-code (Yash half) | P0 | Yash half DONE (contracts + scaffold pushed). Shiva half (rocket/trajectory/manifest/result dummies, env) pending | Shiva dummies |
| 01 | Catalog pipeline and frames | Yash | claude-code | P0 | done; complete catalog via OMM (19,565 incl. 1,010 numbered >= 100000) | - |
| 02 | Launch windows and phasing | Yash | claude-code | P0 | done (real ISS/GPS windows; geo_comm placeholder PROVISIONAL) | - |
| 03 | Frontend v0 | Yash | claude-code | P0 | v0 done (2D map, real windows + Shiva's real trajectory) | - |
| 04 | Deploy v1 | Yash | claude-code | P0 | LIVE: https://maxq-nu.vercel.app (redeployed 05:25 with Sky tab + WP-16) | - |
| 05 | Ascent simulator | Shiva | - | P0 | done (frame fix verified by Yash 04:05) | - |
| 06 | Pitch/staging optimizer | Shiva | - | P1 | done (CMA-ES; merged to main) | - |
| 07 | Rocket design search | Shiva | - | P1 | done (NSGA-II design search) | - |
| 08 | Collision screening | Yash | claude-code | P1 | done (ascent + 2 h coast; all go; 6-digit catalog gap noted) | - |
| 09 | Catalog clustering and congestion | Yash | claude-code | P1 | done (DBSCAN, 24 clusters, Sky tab; live) | - |
| 10 | Manifest scheduler | Shiva | - | P1 | done; verified at 05:55 (artifacts regenerated, committed instances equal fresh builds, results feasible) | - |
| 11 | QAOA benchmark | Shiva | - | P2 | verified except: generated paragraph says p=2 'recovers the optimum' (it scored 80.5%): fix wording, widen plot axis | - |
| 12 | Profiles UI, README, demo | Yash | - | P1 | DEFERRED to last stage (Yash decision 03:35); README/demo/Risk+Manifest tabs after the 06:30 freeze | - |
| 13 | LLM mission interface | Yash | - | P2 | not started | - |
| 14 | Scheduler warm-start model | Shiva | - | P2 | done; fixes verified (instance-level ROC-AUC 0.872, rank-by-value baseline 0.800) | - |
| 15 | SGP4 residual correction | first one free | claude-code (rework) | P2 | done with REAL data (Yash fetched Space-Track history): held-out RMS -19.7 % (90% CI 16-24 %), drift-only no help; see decisions.md 06:45 | - |
| 16 | Space-weather overlay | Yash | claude-code | P2 | done (NOAA Kp chip + Sky panel, display only) | - |

## Notes
- Contracts landed on `main` at 01:2x IST (commit `contract: add schemas and validator`). Consumers: use `from contracts.validate import validate`.

## Phase 2 (HANDOFF_PHASE2.md) - added 2026-11-11

| WP | Title | Owner | Agent/tool | Tier | State | Blocker |
|----|-------|-------|------------|------|-------|---------|
| WP2-00 | Scaffold, contracts, dummy data | shared | claude-code (Yash) | A | done: 8 schemas, dummies, validator, tests | - |
| WP2-01 | Real rocket and pad library (cited) | Yash | - | A | not started | - |
| WP2-02 | MissionEnv core (plan + step mode) | Shiva | - | A | not started | - |
| WP2-03 | Phasing and rendezvous physics (HCW) | Shiva | - | A/B | not started | WP2-02 |
| WP2-04 | Verifier and cost (gated) | Shiva | - | A | not started | - |
| WP2-05 | Baseline optimizer, attempt log, best-known runner | Shiva | - | A | not started | WP2-02, WP2-04 |
| WP2-06 | API + session streaming + replay recording | Yash | - | A | not started | - |
| WP2-07 | UI: mission wizard | Yash | - | A | not started | WP2-01 |
| WP2-08 | UI: mission control panels | Yash | - | A | not started | - |
| WP2-09 | UI: 3D scene and cameras | Yash | claude-code | B | in progress (v1 WP-17 simulator; flight cameras mid-fix) | - |
| WP2-10 | Experience memory + warm-start benchmark | Yash | - | B | not started | WP2-05 |
| WP2-11 | Agent orchestrator (LLM tools + OptimizerAgent) | Yash | - | B | not started | WP2-04, WP2-05 |
| WP2-12 | Benchmark suite, protocol, ablation runner | shared | - | B | not started | protocol is Red |
| WP2-13 | Fine-tuning | Shiva | - | C | not started | GPU decision |
| WP2-14 | RL guidance policy | Shiva / first free | - | C | not started | - |
| WP2-15 | GPS/GEO profiles, real GEO windows | Yash | - | C | not started | - |
| WP2-16 | README, demo recording, write-up | Yash | - | B | not started | - |
