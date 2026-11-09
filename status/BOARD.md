# MaxQ Board (maintained by Yash)

Sprint: 2026-11-10, 00:30 -> 07:30 IST. Tiers: P0 deploy by 04:30, P1 freeze 06:30, P2 if ahead.

| WP | Title | Owner | Agent/tool | Tier | State | Blocker |
|----|-------|-------|------------|------|-------|---------|
| 00 | Kickoff and scaffolding | Yash + Shiva | claude-code (Yash half) | P0 | Yash half DONE (contracts + scaffold pushed). Shiva half (rocket/trajectory/manifest/result dummies, env) pending | Shiva dummies |
| 01 | Catalog pipeline and frames | Yash | claude-code | P0 | done; complete catalog via OMM (19,565 incl. 1,010 numbered >= 100000) | - |
| 02 | Launch windows and phasing | Yash | claude-code | P0 | done (real ISS/GPS windows; geo_comm placeholder PROVISIONAL) | - |
| 03 | Frontend v0 | Yash | claude-code | P0 | v0 done (2D map, real windows + Shiva's real trajectory) | - |
LIVE: https://maxq-nu.vercel.app (redeployed 05:25 with Sky tab + WP-16) | - |
| 05 | Ascent simulator | Shiva | - | P0 | done (frame fix verified by Yash 04:05) | - |
| 06 | Pitch/staging optimizer | Shiva | - | P1 | done (CMA-ES; merged to main) | - |
| 07 | Rocket design search | Shiva | - | P1 | done (NSGA-II design search) | - |
| 08 | Collision screening | Yash | claude-code | P1 | done (ascent + 2 h coast; all go; 6-digit catalog gap noted) | - |
| 09 | Catalog clustering and congestion | Yash | claude-code | P1 | done (DBSCAN, 24 clusters, Sky tab; live) | - |
code fixes verified; committed instances/results/plots must be regenerated (see status/shiva.md 05:50) | - |
control + paragraph in code; committed qaoa_benchmark.png still lacks the control: regenerate | - |
| 12 | Profiles UI, README, demo | Yash | - | P1 | DEFERRED to last stage (Yash decision 03:35); README/demo/Risk+Manifest tabs after the 06:30 freeze | - |
| 13 | LLM mission interface | Yash | - | P2 | not started | - |
done; fixes verified (instance-level AUC 0.872, baseline 0.800) | - |
| 15 | SGP4 residual correction | first one free | - | P2 | not started | Space-Track account |
| 16 | Space-weather overlay | Yash | claude-code | P2 | done (NOAA Kp chip + Sky panel, display only) | - |

## Notes
- Contracts landed on `main` at 01:2x IST (commit `contract: add schemas and validator`). Consumers: use `from contracts.validate import validate`.
