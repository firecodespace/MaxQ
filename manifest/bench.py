# ruff: noqa: E402
"""Launch manifest scheduling benchmark suite.

Compares Greedy, Simulated Annealing (QUBO), and OR-Tools CP-SAT across
manifest instances of ~12, ~50, ~200, and ~1000 tasks.
Generates quality-vs-runtime plots and contract-validated results.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())
import matplotlib.pyplot as plt

from contracts.validate import validate
from manifest.instance import build_instance
from manifest.solvers.cpsat import solve_cpsat
from manifest.solvers.greedy import solve_greedy
from manifest.solvers.sa import solve_sa

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "data" / "results"


def run_benchmark(
    sizes: tuple[int, ...] = (12, 50, 200, 1000),
    seed: int = 42,
    save_results: bool = True,
) -> dict[str, Any]:
    """Execute manifest scheduling benchmark across standard instance sizes."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    benchmark_records: list[dict[str, Any]] = []

    header = (
        f"{'Instance':<20} {'Tasks':<6} {'Conflicts':<10} {'Solver':<10} "
        f"{'Score':<10} {'% Opt':<8} {'Time (s)':<10} {'Status'}"
    )
    print(header)
    print("-" * 88)

    for n_tasks in sizes:
        inst = build_instance(num_tasks=n_tasks, seed=seed, instance_id=f"manifest_{n_tasks}_tasks")

        # Validate instance against contract
        validate("manifest_instance", inst.to_dict())

        if save_results:
            inst.save(RESULTS_DIR / f"manifest_instance_{n_tasks}.json")

        n_conflicts = len(inst.conflicts)

        # 1. Exact reference: CP-SAT
        t_cpsat_limit = 20.0 if n_tasks <= 200 else 30.0
        res_cpsat = solve_cpsat(inst, time_limit_s=t_cpsat_limit, seed=seed)
        validate("result", res_cpsat)
        opt_score = float(res_cpsat["score"])
        cpsat_valid = inst.is_conflict_free(res_cpsat["selected"])

        if save_results:
            with open(RESULTS_DIR / f"result_manifest_{n_tasks}_tasks_cpsat.json", "w", encoding="utf-8") as f:
                json.dump(res_cpsat, f, indent=2)

        benchmark_records.append({
            "instance": inst.id,
            "tasks": n_tasks,
            "conflicts": n_conflicts,
            "solver": "cpsat",
            "score": opt_score,
            "opt_ratio": 1.0,
            "runtime_s": res_cpsat["runtime_s"],
            "selected_count": len(res_cpsat["selected"]),
            "valid": cpsat_valid,
        })
        status_cp = "OPT" if cpsat_valid else "FAIL"
        print(
            f"{inst.id:<20} {n_tasks:<6} {n_conflicts:<10} {'cpsat':<10} "
            f"{opt_score:<10.2f} {'100.0%':<8} {res_cpsat['runtime_s']:<10.4f} {status_cp}"
        )

        # 2. Heuristic baseline: Greedy
        res_greedy = solve_greedy(inst, seed=seed)
        validate("result", res_greedy)
        greedy_score = float(res_greedy["score"])
        greedy_ratio = (greedy_score / opt_score) if opt_score > 0 else 1.0
        greedy_valid = inst.is_conflict_free(res_greedy["selected"])

        if save_results:
            with open(RESULTS_DIR / f"result_manifest_{n_tasks}_tasks_greedy.json", "w", encoding="utf-8") as f:
                json.dump(res_greedy, f, indent=2)

        benchmark_records.append({
            "instance": inst.id,
            "tasks": n_tasks,
            "conflicts": n_conflicts,
            "solver": "greedy",
            "score": greedy_score,
            "opt_ratio": greedy_ratio,
            "runtime_s": res_greedy["runtime_s"],
            "selected_count": len(res_greedy["selected"]),
            "valid": greedy_valid,
        })
        status_gr = "OK" if greedy_valid else "FAIL"
        print(
            f"{inst.id:<20} {n_tasks:<6} {n_conflicts:<10} {'greedy':<10} "
            f"{greedy_score:<10.2f} {greedy_ratio*100:<7.1f}% {res_greedy['runtime_s']:<10.6f} {status_gr}"
        )

        # 3. QUBO solver: Simulated Annealing
        res_sa = solve_sa(inst, seed=seed)
        validate("result", res_sa)
        sa_score = float(res_sa["score"])
        sa_ratio = (sa_score / opt_score) if opt_score > 0 else 1.0
        sa_valid = inst.is_conflict_free(res_sa["selected"])

        if save_results:
            with open(RESULTS_DIR / f"result_manifest_{n_tasks}_tasks_sa.json", "w", encoding="utf-8") as f:
                json.dump(res_sa, f, indent=2)

        benchmark_records.append({
            "instance": inst.id,
            "tasks": n_tasks,
            "conflicts": n_conflicts,
            "solver": "sa",
            "score": sa_score,
            "opt_ratio": sa_ratio,
            "runtime_s": res_sa["runtime_s"],
            "selected_count": len(res_sa["selected"]),
            "valid": sa_valid,
        })
        status_sa = "OK" if sa_valid else "FAIL"
        print(
            f"{inst.id:<20} {n_tasks:<6} {n_conflicts:<10} {'sa':<10} "
            f"{sa_score:<10.2f} {sa_ratio*100:<7.1f}% {res_sa['runtime_s']:<10.4f} {status_sa}"
        )
        print("-" * 88)

    # Generate benchmark plot
    plot_path = plot_benchmark(benchmark_records, sizes)
    print(f"\nBenchmark visualization saved to: {plot_path}")

    return {"records": benchmark_records, "plot_path": str(plot_path)}


def plot_benchmark(records: list[dict[str, Any]], sizes: tuple[int, ...]) -> Path:
    """Generate quality vs runtime benchmark visualization."""
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16, 4.8), dpi=150)
    fig.patch.set_facecolor("#0b0f19")

    colors = {
        "cpsat": "#38bdf8",   # Bright Cyan
        "sa": "#a855f7",      # Purple
        "greedy": "#22c55e",  # Emerald Green
    }
    labels = {
        "cpsat": "OR-Tools CP-SAT (Exact)",
        "sa": "Simulated Annealing (QUBO)",
        "greedy": "Greedy Priority Heuristic",
    }
    markers = {"cpsat": "o", "sa": "s", "greedy": "^"}

    for s_name in ("cpsat", "sa", "greedy"):
        sub = [r for r in records if r["solver"] == s_name]
        x_vals = [r["tasks"] for r in sub]
        scores = [r["score"] for r in sub]
        ratios = [r["opt_ratio"] * 100 for r in sub]
        runtimes = [max(1e-6, r["runtime_s"]) for r in sub]

        ax1.plot(
            x_vals, scores, marker=markers[s_name], color=colors[s_name],
            label=labels[s_name], linewidth=2, markersize=7
        )
        ax2.plot(
            x_vals, ratios, marker=markers[s_name], color=colors[s_name],
            label=labels[s_name], linewidth=2, markersize=7
        )
        ax3.plot(
            x_vals, runtimes, marker=markers[s_name], color=colors[s_name],
            label=labels[s_name], linewidth=2, markersize=7
        )

    # Style axes
    for ax in (ax1, ax2, ax3):
        ax.set_facecolor("#111827")
        ax.tick_params(colors="#9ca3af", which="both")
        for spine in ax.spines.values():
            spine.set_color("#374151")
        ax.grid(True, linestyle="--", alpha=0.3, color="#4b5563")
        ax.set_xlabel("Instance Size (Candidate Tasks)", color="#e5e7eb", fontsize=10)

    # Panel 1: Solution Value
    ax1.set_title("Solution Quality (Total Value)", color="#f9fafb", fontsize=12, fontweight="bold")
    ax1.set_ylabel("Total Scheduled Value", color="#e5e7eb", fontsize=10)
    ax1.set_xscale("log")

    # Panel 2: Approximation Ratio
    ax2.set_title("Approximation Ratio (% of Optimal)", color="#f9fafb", fontsize=12, fontweight="bold")
    ax2.set_ylabel("Optimality Ratio (%)", color="#e5e7eb", fontsize=10)
    ax2.set_ylim(70, 105)
    ax2.set_xscale("log")
    ax2.axhline(100, color="#38bdf8", linestyle=":", alpha=0.6)

    # Panel 3: Runtime
    ax3.set_title("Wall-Clock Runtime (Seconds)", color="#f9fafb", fontsize=12, fontweight="bold")
    ax3.set_ylabel("Runtime (s) - Log Scale", color="#e5e7eb", fontsize=10)
    ax3.set_yscale("log")
    ax3.set_xscale("log")
    ax3.legend(facecolor="#1f2937", edgecolor="#374151", labelcolor="#e5e7eb", fontsize=9, loc="upper left")

    plt.suptitle(
        "MaxQ Launch Manifest Scheduler Benchmark (WP-10)",
        color="#f9fafb", fontsize=14, y=1.02, fontweight="bold"
    )
    plt.tight_layout()

    out_file = RESULTS_DIR / "manifest_benchmark.png"
    plt.savefig(out_file, dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return out_file


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for make manifest with argparse support."""
    parser = argparse.ArgumentParser(
        description="MaxQ launch manifest scheduler benchmark (WP-10)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=[12, 50, 200, 1000],
        help="Instance sizes to benchmark (number of candidate tasks)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible instance generation and solvers",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Run benchmark without overwriting committed files in data/results",
    )

    args = parser.parse_args(argv)

    t0 = time.perf_counter()
    print("=" * 88)
    print("MaxQ Launch Manifest Scheduler Benchmark (WP-10)")
    print("=" * 88)
    run_benchmark(sizes=tuple(args.sizes), seed=args.seed, save_results=not args.no_save)
    elapsed = time.perf_counter() - t0
    print(f"\nAll benchmark runs completed and validated in {elapsed:.2f} s.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
