"""Long-budget multi-seed best-known mission optimizer runner (WP2-05).

Runs `optimize_mission` across multiple independent seeds at higher evaluation
budgets to establish reproducible best-known reference solutions (never claimed
as global optima). Records every seed, budget, `sim_version`, and `verifier_version`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from contracts.validate import validate
from optimize.cma_optimizer import optimize_mission

ROOT = Path(__file__).resolve().parents[1]


def run_best_known(
    mission_request: dict[str, Any],
    seeds: Sequence[int] = (11, 22, 33, 42, 77),
    budget_per_seed: int = 80,
    out_path: Path | str | None = None,
    save_attempts: bool = False,
) -> dict[str, Any]:
    """Execute multi-seed CMA-ES runs and summarize the best-known solution."""
    if not seeds:
        raise ValueError("At least one seed is required")
    if budget_per_seed <= 0:
        raise ValueError("budget_per_seed must be positive")

    validate("mission_request", mission_request)
    mission_id = str(mission_request.get("id", "msn_best_known"))
    wall_start = time.perf_counter()

    seed_summaries: list[dict[str, Any]] = []
    best_attempt: dict[str, Any] | None = None

    for seed in seeds:
        req_seed = dict(mission_request)
        req_seed["seed"] = int(seed)
        best_s = optimize_mission(
            mission_request=req_seed,
            max_attempts=int(budget_per_seed),
            seed=int(seed),
            session_log_path=None,
            save_attempts=save_attempts,
        )
        validate("attempt", best_s)
        seed_summaries.append(
            {
                "seed": int(seed),
                "evaluations": int(budget_per_seed),
                "feasible": bool(best_s["feasible"]),
                "cost": float(best_s["cost"]),
                "prop_used_kg": best_s["metrics"].get("prop_used_kg"),
                "time_to_dock_s": best_s["metrics"].get("time_to_dock_s"),
            }
        )
        if best_attempt is None or float(best_s["cost"]) < float(best_attempt["cost"]):
            best_attempt = best_s

    assert best_attempt is not None
    feasible_seeds = [s for s in seed_summaries if s["feasible"]]

    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "best_known_measured",
        "mission_id": mission_id,
        "sim_version": best_attempt["sim_version"],
        "verifier_version": best_attempt["verifier_version"],
        "seeds": [int(s) for s in seeds],
        "budget_per_seed": int(budget_per_seed),
        "total_evaluations": int(budget_per_seed) * len(seed_summaries),
        "feasible_seed_count": len(feasible_seeds),
        "best_seed": int(best_attempt["seed"]),
        "best_cost": float(best_attempt["cost"]),
        "best_feasible": bool(best_attempt["feasible"]),
        "seed_results": seed_summaries,
        "best_attempt": best_attempt,
        "wall_s": round(time.perf_counter() - wall_start, 3),
    }

    if out_path is not None:
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MaxQ WP2-05 Multi-Seed Best-Known Runner")
    parser.add_argument("--mission", type=Path, required=True, help="Path to mission_request.json")
    parser.add_argument("--budget", type=int, default=80, help="Evaluations per seed")
    parser.add_argument("--seeds", type=int, nargs="+", default=[11, 22, 33, 42, 77])
    parser.add_argument("--out", type=Path, default=None, help="Output JSON report path")
    args = parser.parse_args(argv)

    req = json.loads(args.mission.read_text(encoding="utf-8"))
    out_path = args.out or (ROOT / "data" / "results" / f"best_known_{req.get('id', 'mission')}.json")
    report = run_best_known(req, seeds=args.seeds, budget_per_seed=args.budget, out_path=out_path)
    print(
        f"Best-known for {report['mission_id']}: feasible={report['best_feasible']} "
        f"cost={report['best_cost']:.6f} (seed={report['best_seed']}, "
        f"evals={report['total_evaluations']}) -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

