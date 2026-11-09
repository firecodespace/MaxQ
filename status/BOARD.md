# MaxQ Board (maintained by Yash)

Sprint: 2026-11-10, 00:30 -> 07:30 IST. Tiers: P0 deploy by 04:30, P1 freeze 06:30, P2 if ahead.

| WP | Title | Owner | Agent/tool | Tier | State | Blocker |
|----|-------|-------|------------|------|-------|---------|
| 00 | Kickoff and scaffolding | Yash + Shiva | claude-code (Yash half) | P0 | Yash half DONE (contracts + scaffold pushed). Shiva half (rocket/trajectory/manifest/result dummies, env) pending | Shiva dummies |
| 01 | Catalog pipeline and frames | Yash | claude-code | P0 | done (TLE-only catalog confirmed by Yash; revisit OMM at WP-08) | - |
| 02 | Launch windows and phasing | Yash | claude-code | P0 | done (real ISS/GPS windows; geo_comm placeholder PROVISIONAL) | - |
| 03 | Frontend v0 | Yash | claude-code | P0 | v0 done (2D map, real windows + Shiva's real trajectory) | - |
| 04 | Deploy v1 | Yash | claude-code | P0 | LIVE: https://maxq-nu.vercel.app (Vercel, personal hobby scope) | - |
| 05 | Ascent simulator | Shiva | - | P0 | not started | contracts (done) |
| 06 | Pitch/staging optimizer | Shiva | - | P1 | not started | WP-05 |
| 07 | Rocket design search | Shiva | - | P1 | not started | WP-05 |
| 08 | Collision screening | Yash | claude-code | P1 | done (ascent-only; coast blocked on trajectory frame question, see status/shiva.md) | Shiva: trajectory frame |
| 09 | Catalog clustering and congestion | Yash | - | P1 | not started | WP-01 |
| 10 | Manifest scheduler | Shiva | - | P1 | not started | WP-02 |
| 11 | QAOA benchmark | Shiva | - | P2 | not started | WP-10 |
| 12 | Profiles UI, README, demo | Yash | - | P1 | not started | WP-03 |
| 13 | LLM mission interface | Yash | - | P2 | not started | - |
| 14 | Scheduler warm-start model | Shiva | - | P2 | not started | WP-10 |
| 15 | SGP4 residual correction | first one free | - | P2 | not started | Space-Track account |
| 16 | Space-weather overlay | Yash | - | P2 | not started | - |

## Notes
- Contracts landed on `main` at 01:2x IST (commit `contract: add schemas and validator`). Consumers: use `from contracts.validate import validate`.
