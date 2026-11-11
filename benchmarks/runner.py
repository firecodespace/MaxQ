"""The benchmark runner (WP2-12): measure a system on a split, and say what was not measured.

    python -m benchmarks.runner splits                       # show the protocol and splits
    python -m benchmarks.runner bounds                       # analytic floors, no simulation
    python -m benchmarks.runner best-known --split train     # the long reference search
    python -m benchmarks.runner run cma_cold --split test    # measure one system
    python -m benchmarks.runner table                        # the results table

Two rules this module exists to keep
------------------------------------
**A system that did not run gets reported as not run.** `cma_memory` needs WP2-10 and
`llm_base` / `llm_ft` need a model that has not been chosen (RED, Yash's). They appear in the
table as `not_run` with the reason, never as a blank or a zero. A benchmark table whose empty
cells look like results is worse than no table.

**A gap is only computed against a comparable best-known.** Best-known costs carry the
simulator and verifier versions they were measured under. If those differ from the live ones,
the gap is `null` and the row says `best_known_stale`. Charter addition 4 asks for versioning;
this is the place it has to bite, because a cost from a different simulator looks exactly like
a cost from this one.

Writes `benchmarks/benchmark.json` (contract 6.6) and, through the runner's capability only,
`benchmarks/hof.json`.

Owner: Yash (lane: benchmarks/). Read-only to any agent.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from benchmarks import hof, lower_bound, protocol, suite
from contracts.validate import validate
from env.mission_env import MissionEnv
from optimize.cma_optimizer import optimize_mission
from verifier import VERIFIER_VERSION

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_PATH = ROOT / "benchmarks" / "benchmark.json"
RESULTS_DIR = ROOT / "benchmarks" / "results"

#: Why each system cannot run yet. A system absent from this map is runnable.
NOT_RUNNABLE: dict[str, str] = {
    "cma_memory": "WP2-10 experience memory does not exist yet, so there is nothing to warm-start from",
    "llm_base": "no open-weight model has been chosen; model and licence selection is Yash's (RED)",
    "llm_ft": "needs llm_base plus WP2-13 fine-tuning, neither of which exists",
}


def sim_version() -> str:
    """The simulator version, taken from an actual attempt rather than recomputed here."""
    att, _ = MissionEnv(suite.missions()[0]).evaluate_plan({}, attempt_id="att_probe")
    return str(att["sim_version"])


# ---------------------------------------------------------------------------
# Measuring one system


def run_system(
    system: str,
    split: str,
    *,
    budget: int | None = None,
    seeds: tuple[int, ...] | None = None,
    progress: bool = True,
) -> dict[str, Any]:
    """Run one system across one split and return its per-mission results.

    Each mission is run once per protocol seed. A mission's result is the best FEASIBLE
    attempt across those seeds; if no seed produced a feasible attempt, `best_cost` is None
    and `feasible_seeds` is 0 -- not the lowest infeasible penalty score, which would read as
    a mission cost.
    """
    if system in NOT_RUNNABLE:
        return {
            "system": system,
            "split": split,
            "status": "not_run",
            "reason": NOT_RUNNABLE[system],
            "missions": {},
        }
    if system not in protocol.SYSTEMS:
        raise SystemExit(f"unknown system {system!r}; the protocol defines {protocol.SYSTEMS}")

    budget = budget or suite.BUDGET_MAX_ATTEMPTS
    seeds = seeds or protocol.SYSTEM_SEEDS
    mission_ids = protocol.splits()[split]
    out: dict[str, Any] = {}
    t_start = time.perf_counter()

    for i, mid in enumerate(mission_ids, 1):
        request = dict(suite.mission_by_id(mid))
        per_seed: list[dict[str, Any]] = []
        for seed in seeds:
            req = dict(request)
            req["seed"] = int(seed)
            best = optimize_mission(
                mission_request=req,
                max_attempts=budget,
                seed=int(seed),
                session_log_path=None,
                save_attempts=False,
            )
            per_seed.append(
                {
                    "seed": int(seed),
                    "feasible": bool(best["feasible"]),
                    "cost": float(best["cost"]),
                    "attempt_id": best["id"],
                    "metrics": best.get("metrics", {}),
                }
            )
        feasible = [s for s in per_seed if s["feasible"]]
        champion = min(feasible, key=lambda s: s["cost"]) if feasible else None
        out[mid] = {
            "best_cost": champion["cost"] if champion else None,
            "best_attempt_id": champion["attempt_id"] if champion else None,
            "best_seed": champion["seed"] if champion else None,
            "feasible_seeds": len(feasible),
            "total_seeds": len(per_seed),
            "metrics": champion["metrics"] if champion else {},
            "per_seed": per_seed,
        }
        if progress:
            cost = f"{champion['cost']:.6f}" if champion else "none (no feasible attempt)"
            print(
                f"  [{i}/{len(mission_ids)}] {mid}: {len(feasible)}/{len(per_seed)} seeds "
                f"feasible, best {cost}",
                flush=True,
            )

    return {
        "system": system,
        "split": split,
        "status": "ran",
        "budget_per_seed": budget,
        "seeds": list(seeds),
        "sim_version": sim_version(),
        "verifier_version": VERIFIER_VERSION,
        "protocol_hash": protocol.protocol_hash(),
        "wall_s": round(time.perf_counter() - t_start, 1),
        "missions": out,
    }


# ---------------------------------------------------------------------------
# Best-known reference search


def run_best_known(split: str, *, budget: int | None = None, progress: bool = True) -> dict[str, Any]:
    """The long-budget multi-seed reference search, and the only writer of the Hall of Fame.

    Far more budget and more seeds than any system is given, because its job is to be hard to
    beat rather than to be fair. Still never called "the optimum": it is the best we have
    found, and a later run with more budget may lower it.
    """
    budget = budget or protocol.BEST_KNOWN_BUDGET_PER_SEED
    seeds = protocol.BEST_KNOWN_SEEDS
    writer = hof.open_for_runner(protocol.protocol_hash())
    sv, vv = sim_version(), VERIFIER_VERSION
    recorded, unchanged, infeasible = [], [], []

    for i, mid in enumerate(protocol.splits()[split], 1):
        request = dict(suite.mission_by_id(mid))
        champion: dict[str, Any] | None = None
        for seed in seeds:
            req = dict(request)
            req["seed"] = int(seed)
            best = optimize_mission(
                mission_request=req, max_attempts=budget, seed=int(seed),
                session_log_path=None, save_attempts=False,
            )
            if best["feasible"] and (champion is None or best["cost"] < champion["cost"]):
                champion = best
        if champion is None:
            infeasible.append(mid)
            if progress:
                print(f"  [{i}] {mid}: no feasible attempt in {budget * len(seeds)} sims", flush=True)
            continue
        changed = writer.record(
            mid,
            cost=float(champion["cost"]),
            attempt_id=str(champion["id"]),
            budget_sims=budget * len(seeds),
            seeds=[int(s) for s in seeds],
            sim_version=sv,
            verifier_version=vv,
            feasible=True,
            metrics=champion.get("metrics", {}),
        )
        (recorded if changed else unchanged).append(mid)
        if progress:
            print(
                f"  [{i}] {mid}: best feasible {champion['cost']:.6f} "
                f"({'recorded' if changed else 'no improvement'})",
                flush=True,
            )

    return {
        "split": split,
        "budget_per_seed": budget,
        "seeds": list(seeds),
        "recorded": recorded,
        "unchanged": unchanged,
        "no_feasible_solution": infeasible,
        "sim_version": sv,
        "verifier_version": vv,
    }


# ---------------------------------------------------------------------------
# The benchmark.json contract file, and the table


def build_benchmark_file(write: bool = True) -> dict[str, Any]:
    """Assemble `benchmarks/benchmark.json` per contract 6.6 from the live protocol."""
    sv, vv = sim_version(), VERIFIER_VERSION
    status = hof.verify_entries(sv, vv)
    entries = hof.read()["entries"]

    best_known: dict[str, Any] = {}
    for mid in status["current"]:
        e = entries[mid]
        best_known[mid] = {
            "cost": e["cost"],
            "attempt_id": e["attempt_id"],
            "budget_sims": e["budget_sims"],
            "seeds": e.get("seeds", []),
        }

    doc = {
        "schema_version": 1,
        "_note": (
            f"Suite {suite.SUITE_ID}, {len(suite.missions())} missions, splits partitioned by "
            f"launch window so no window appears in two splits. best_known holds only the "
            f"{len(status['current'])} mission(s) measured under the live sim_version {sv} and "
            f"verifier_version {vv}; {len(status['stale'])} recorded under earlier versions are "
            f"omitted rather than silently compared. lower_bound is analytic and loose -- a "
            f"floor, not a target. Missions absent from best_known have no known feasible "
            f"solution yet."
        ),
        "suite_id": suite.SUITE_ID,
        "protocol_hash": protocol.protocol_hash(),
        "splits": protocol.splits(),
        "best_known": best_known,
        "lower_bound": {
            m["id"]: {
                "dv_mps": lower_bound.for_mission(m)["dv_mps"],
                "prop_kg": lower_bound.for_mission(m)["prop_kg"],
            }
            for m in suite.missions()
        },
        "systems": {
            s: (
                {"status": "not_run", "reason": NOT_RUNNABLE[s]}
                if s in NOT_RUNNABLE
                else {"status": "runnable"}
            )
            for s in protocol.SYSTEMS
        },
    }
    validate("benchmark", doc)
    if write:
        BENCHMARK_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def _result_files() -> list[Path]:
    return sorted(RESULTS_DIR.glob("*.json")) if RESULTS_DIR.exists() else []


def table() -> str:
    """The results table: gap to best-known per system, with every absence named."""
    sv, vv = sim_version(), VERIFIER_VERSION
    entries = hof.read()["entries"]
    rows: list[str] = []
    rows.append(f"sim_version {sv} | verifier_version {vv}")
    rows.append(f"protocol {protocol.protocol_hash()}")
    rows.append("")

    results = {}
    for path in _result_files():
        r = json.loads(path.read_text(encoding="utf-8"))
        results[(r["system"], r["split"])] = r

    for system in protocol.SYSTEMS:
        if system in NOT_RUNNABLE:
            rows.append(f"{system:<12} not_run  -- {NOT_RUNNABLE[system]}")
            continue
        ran = [k for k in results if k[0] == system]
        if not ran:
            rows.append(f"{system:<12} not_run  -- no results file under benchmarks/results/")
            continue
        for _, split in sorted(ran):
            r = results[(system, split)]
            if r.get("sim_version") != sv or r.get("verifier_version") != vv:
                rows.append(
                    f"{system:<12} {split:<6} STALE -- measured under sim {r.get('sim_version')} "
                    f"/ verifier {r.get('verifier_version')}; not comparable"
                )
                continue
            rows.append(f"{system:<12} {split:<6} budget {r['budget_per_seed']}/seed, "
                        f"seeds {r['seeds']}")
            rows.append(f"  {'mission':<34} {'best cost':>11} {'gap %':>9} {'feas':>5} converged")
            for mid, m in r["missions"].items():
                e = entries.get(mid)
                bk_ok = e is not None and (
                    e.get("sim_version") == sv and e.get("verifier_version") == vv
                )
                gap = protocol.gap_pct(m["best_cost"], e["cost"] if bk_ok else None)
                if m["best_cost"] is None:
                    cost_s = "none"
                else:
                    cost_s = f"{m['best_cost']:.6f}"
                if gap is not None:
                    gap_s = f"{gap:+.2f}"
                elif e is None:
                    gap_s = "no best-known"
                elif not bk_ok:
                    gap_s = "bk stale"
                else:
                    gap_s = "n/a"
                conv = protocol.is_converged(gap, m["feasible_seeds"])
                rows.append(
                    f"  {mid.replace('msn_bench_', ''):<34} {cost_s:>11} {gap_s:>9} "
                    f"{m['feasible_seeds']}/{m['total_seeds']:<3} {'yes' if conv else 'no'}"
                )
            rows.append("")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MaxQ WP2-12 benchmark runner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("splits", help="print the protocol, splits and hash")
    sub.add_parser("bounds", help="print the analytic lower bounds (no simulation)")
    sub.add_parser("table", help="print the results table")
    p_build = sub.add_parser("build", help="write benchmarks/benchmark.json")
    p_build.add_argument("--dry-run", action="store_true")

    p_bk = sub.add_parser("best-known", help="run the long reference search; updates hof.json")
    p_bk.add_argument("--split", default="train", choices=list(suite.SPLIT_NAMES))
    p_bk.add_argument("--budget", type=int, default=None)

    p_run = sub.add_parser("run", help="measure one system on one split")
    p_run.add_argument("system", choices=list(protocol.SYSTEMS))
    p_run.add_argument("--split", default="val", choices=list(suite.SPLIT_NAMES))
    p_run.add_argument("--budget", type=int, default=None)
    p_run.add_argument("--out", type=Path, default=None)

    args = ap.parse_args(argv)

    if args.cmd == "splits":
        sp = protocol.splits()
        print(f"suite {suite.SUITE_ID}: {len(suite.missions())} missions")
        print(f"protocol_hash {protocol.protocol_hash()}")
        for name, ids in sp.items():
            windows = sorted({suite.mission_by_id(m)["window_id"] for m in ids})
            print(f"\n{name} ({len(ids)} missions, windows {windows}):")
            for mid in ids:
                print(f"  {mid}")
        return 0

    if args.cmd == "bounds":
        print(f"{'mission':<34} {'dv floor':>10} {'prop floor':>12} {'cost floor':>11}")
        for m in suite.missions():
            b = lower_bound.for_mission(m)
            print(
                f"{m['id'].replace('msn_bench_', ''):<34} {b['dv_mps']:>10,.0f} "
                f"{b['prop_kg']:>12,.0f} {b['cost']:>11.5f}"
            )
        print("\nA floor, not a target: lossless ascent, massless structure, zero phasing cost.")
        return 0

    if args.cmd == "build":
        doc = build_benchmark_file(write=not args.dry_run)
        print(json.dumps(doc, indent=2) if args.dry_run else f"wrote {BENCHMARK_PATH}")
        return 0

    if args.cmd == "table":
        print(table())
        return 0

    if args.cmd == "best-known":
        print(f"best-known search on split {args.split}")
        rep = run_best_known(args.split, budget=args.budget)
        print(
            f"\nrecorded {len(rep['recorded'])}, unchanged {len(rep['unchanged'])}, "
            f"no feasible solution {len(rep['no_feasible_solution'])}"
        )
        if rep["no_feasible_solution"]:
            print("  no feasible solution found for: " + ", ".join(rep["no_feasible_solution"]))
        build_benchmark_file()
        return 0

    if args.cmd == "run":
        print(f"{args.system} on split {args.split}")
        rep = run_system(args.system, args.split, budget=args.budget)
        if rep["status"] == "not_run":
            print(f"not run: {rep['reason']}")
            return 0
        out = args.out or (RESULTS_DIR / f"{args.system}__{args.split}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out}\n")
        print(table())
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
