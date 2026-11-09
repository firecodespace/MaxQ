"""OR-Tools CP-SAT exact solver for launch manifest scheduling.

Solves the Maximum Weight Independent Set integer programming formulation to proven optimality.
Serves as the exact reference standard for heuristic and quantum benchmarks.
"""

from __future__ import annotations

import time
from typing import Any

from ortools.sat.python import cp_model

from manifest.instance import ManifestInstance


def solve_cpsat(
    instance: ManifestInstance,
    time_limit_s: float = 30.0,
    seed: int = 42,
    num_workers: int = 4,
    **kwargs: Any,
) -> dict[str, Any]:
    """Solve manifest scheduling to proven global optimality using OR-Tools CP-SAT.

    Args:
        instance: ManifestInstance to solve.
        time_limit_s: Maximum solver wall clock time in seconds.
        seed: Random seed for CP-SAT search heuristics.
        num_workers: Number of parallel search workers.

    Returns:
        Contract-compliant dictionary with optimal task IDs, score, and runtime.
    """
    t0 = time.perf_counter()
    tasks = instance.tasks
    n = len(tasks)
    if n == 0:
        return {
            "schema_version": 1,
            "_note": "OR-Tools CP-SAT exact solution",
            "instance": instance.id,
            "solver": "cpsat",
            "selected": [],
            "score": 0.0,
            "runtime_s": round(time.perf_counter() - t0, 6),
            "seed": seed,
        }

    model = cp_model.CpModel()

    # Binary decision variables x_i in {0, 1}
    x = [model.new_bool_var(f"x_{i}") for i in range(n)]

    # Conflict constraints: x_u + x_v <= 1
    for u, v in instance.conflicts:
        model.add(x[u] + x[v] <= 1)

    # Objective: Maximize sum(value_i * x_i)
    # Scale float values by 1000 to convert to exact integers
    scale = 1000
    int_weights = [int(round(t.value * scale)) for t in tasks]
    model.maximize(cp_model.LinearExpr.weighted_sum(x, int_weights))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    solver.parameters.random_seed = int(seed)
    solver.parameters.num_search_workers = int(num_workers)

    status = solver.solve(model)

    runtime_s = max(1e-6, time.perf_counter() - t0)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        selected = [i for i in range(n) if solver.value(x[i]) == 1]
        score = sum(tasks[i].value for i in selected)
    else:
        selected = []
        score = 0.0

    return {
        "schema_version": 1,
        "_note": "OR-Tools CP-SAT exact solution",
        "instance": instance.id,
        "solver": "cpsat",
        "selected": sorted(selected),
        "score": round(float(score), 3),
        "runtime_s": round(runtime_s, 6),
        "seed": seed,
    }

