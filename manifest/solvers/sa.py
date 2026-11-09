"""Simulated Annealing solver for launch manifest scheduling via QUBO.

Minimizes QUBO energy E(x) = -sum(v_i * x_i) + P * sum_{(i,j) in E} (x_i * x_j).
Maintains neighbor conflict counts for O(1) energy delta evaluation per proposal.
Extracts the best conflict-free schedule evaluated during the annealing trajectory.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any

import numpy as np

from manifest.instance import ManifestInstance


def solve_sa(
    instance: ManifestInstance,
    steps: int | None = None,
    t_start: float | None = None,
    t_end: float = 0.005,
    seed: int = 42,
    restarts: int = 2,
    init_selected: list[int] | None = None,
    log_trajectory: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Solve manifest scheduling using Simulated Annealing on QUBO formulation.

    Args:
        instance: ManifestInstance to solve.
        steps: Total proposal iterations per restart (scaled automatically if None).
        t_start: Starting temperature (scaled automatically if None).
        t_end: Final temperature.
        seed: Random seed for deterministic reproducibility.
        restarts: Number of independent annealing runs from different initial seeds.
        init_selected: Optional initial list of active task IDs for warm-starting.
        log_trajectory: Whether to log intermediate best scores along the run.

    Returns:
        Contract-compliant dictionary with selected task IDs, score, and runtime.
    """
    t0 = time.perf_counter()
    n = len(instance.tasks)
    if n == 0:
        return {
            "schema_version": 1,
            "_note": "Simulated Annealing QUBO solution",
            "instance": instance.id,
            "solver": "sa",
            "selected": [],
            "score": 0.0,
            "runtime_s": round(time.perf_counter() - t0, 6),
            "seed": seed,
        }

    # Extract values and adjacency
    values = np.array([t.value for t in instance.tasks], dtype=np.float64)
    max_val = float(np.max(values))
    penalty = max_val * 3.0 + 2.0  # Guarantees penalty > 2 * max_val

    adj: list[list[int]] = [[] for _ in range(n)]
    for u, v in instance.conflicts:
        adj[u].append(v)
        adj[v].append(u)

    # Scale iterations based on instance size
    if steps is None:
        if n <= 20:
            steps = 15000
        elif n <= 100:
            steps = 40000
        elif n <= 300:
            steps = 80000
        else:
            steps = 150000

    if t_start is None:
        t_start = max_val * 2.0

    cooling_rate = (t_end / t_start) ** (1.0 / max(1, steps))

    global_best_selected: list[int] = []
    global_best_score = 0.0
    trajectory: list[tuple[int, float]] = []

    master_rng = random.Random(seed)

    for run_idx in range(restarts):
        run_seed = master_rng.randint(0, 10**8)
        rng = random.Random(run_seed)

        x = [0] * n
        conflict_counts = [0] * n

        # Initial seed: warm-start from provided list, or sample random nodes
        if init_selected is not None:
            # Filter and greedily load provided warm-start task IDs
            seen_forbidden: set[int] = set()
            for i in init_selected:
                if 0 <= i < n and i not in seen_forbidden and conflict_counts[i] == 0:
                    x[i] = 1
                    seen_forbidden.add(i)
                    seen_forbidden.update(adj[i])
                    for neighbor in adj[i]:
                        conflict_counts[neighbor] += 1
        else:
            # Initial random or greedy seed: pick a few high-value nodes without immediate conflict
            for i in rng.sample(range(n), min(n, max(1, n // 5))):
                if conflict_counts[i] == 0:
                    x[i] = 1
                    for neighbor in adj[i]:
                        conflict_counts[neighbor] += 1

        curr_t = t_start
        run_best_selected: list[int] = [i for i in range(n) if x[i] == 1 and conflict_counts[i] == 0]
        run_best_score = sum(values[i] for i in run_best_selected)

        if log_trajectory and run_idx == 0:
            trajectory.append((0, run_best_score))

        log_interval = max(10, steps // 100)

        for step in range(steps):
            k = rng.randint(0, n - 1)
            is_active = x[k]

            # Delta E calculation for flipping bit k
            # E(x) = -sum v_i x_i + P * sum x_i x_j
            # When x_k: 0 -> 1, delta E = -v_k + P * conflict_counts[k]
            # When x_k: 1 -> 0, delta E = +v_k - P * conflict_counts[k]
            if is_active == 0:
                delta_e = -values[k] + penalty * conflict_counts[k]
            else:
                delta_e = values[k] - penalty * conflict_counts[k]

            accept = False
            if delta_e < 0.0:
                accept = True
            else:
                if curr_t > 1e-9:
                    prob = math.exp(-delta_e / curr_t)
                    if rng.random() < prob:
                        accept = True

            if accept:
                new_state = 1 - is_active
                x[k] = new_state
                change = 1 if new_state == 1 else -1
                for neighbor in adj[k]:
                    conflict_counts[neighbor] += change

                # Track best conflict-free solution
                # A state is strictly conflict-free if for all active k (x[k]==1), conflict_counts[k] == 0
                if new_state == 1 and conflict_counts[k] == 0:
                    # Quick feasibility check on active subset
                    active_nodes = [i for i in range(n) if x[i] == 1]
                    has_conflict = any(conflict_counts[i] > 0 for i in active_nodes)
                    if not has_conflict:
                        cand_score = sum(values[i] for i in active_nodes)
                        if cand_score > run_best_score:
                            run_best_score = cand_score
                            run_best_selected = active_nodes[:]

            if log_trajectory and run_idx == 0 and (step % log_interval == 0 or step == steps - 1):
                trajectory.append((step + 1, run_best_score))

            curr_t = max(t_end, curr_t * cooling_rate)

        # Fallback repair on final state if no feasible solution was tracked
        if not run_best_selected:
            active_nodes = [i for i in range(n) if x[i] == 1]
            active_nodes.sort(key=lambda i: values[i], reverse=True)
            repaired: list[int] = []
            seen_forbidden: set[int] = set()
            for u in active_nodes:
                if u not in seen_forbidden:
                    repaired.append(u)
                    seen_forbidden.add(u)
                    seen_forbidden.update(adj[u])
            run_best_selected = repaired
            run_best_score = sum(values[i] for i in repaired)

        if run_best_score > global_best_score:
            global_best_score = run_best_score
            global_best_selected = run_best_selected

    runtime_s = max(1e-6, time.perf_counter() - t0)

    res = {
        "schema_version": 1,
        "_note": "Simulated Annealing QUBO solution",
        "instance": instance.id,
        "solver": "sa",
        "selected": sorted(global_best_selected),
        "score": round(float(global_best_score), 3),
        "runtime_s": round(runtime_s, 6),
        "seed": seed,
    }

    if log_trajectory:
        return res, trajectory

    return res

