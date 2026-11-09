"""Greedy heuristic solver for launch manifest scheduling.

Solves the Maximum Weight Independent Set (MWIS) problem on the conflict graph.
Evaluates weight-to-degree and weight-only priority heuristics and returns the best.
"""

from __future__ import annotations

import time
from typing import Any

from manifest.instance import ManifestInstance


def solve_greedy(
    instance: ManifestInstance,
    heuristic: str = "best",
    seed: int = 42,  # included for interface consistency
    **kwargs: Any,
) -> dict[str, Any]:
    """Solve manifest scheduling using greedy priority heuristics.

    Args:
        instance: ManifestInstance to solve.
        heuristic: 'weight_deg', 'weight', or 'best' (runs both and keeps max score).
        seed: Random seed (unused by deterministic greedy, kept for API uniformity).

    Returns:
        Contract-compliant dictionary with selected task IDs, score, and runtime.
    """
    t0 = time.perf_counter()
    tasks = instance.tasks
    n = len(tasks)
    if n == 0:
        return {
            "schema_version": 1,
            "_note": "Greedy heuristic solution",
            "instance": instance.id,
            "solver": "greedy",
            "selected": [],
            "score": 0.0,
            "runtime_s": round(time.perf_counter() - t0, 6),
            "seed": seed,
        }

    # Build adjacency list
    adj: list[set[int]] = [set() for _ in range(n)]
    for u, v in instance.conflicts:
        adj[u].add(v)
        adj[v].add(u)

    values = [t.value for t in tasks]

    def _run_order(order: list[int]) -> tuple[list[int], float]:
        selected: list[int] = []
        forbidden: set[int] = set()
        score = 0.0

        for u in order:
            if u in forbidden:
                continue
            selected.append(u)
            score += values[u]
            forbidden.add(u)
            forbidden.update(adj[u])

        return sorted(selected), round(score, 3)

    # Heuristic 1: value / (1 + degree)
    order_deg = sorted(range(n), key=lambda i: values[i] / (1.0 + len(adj[i])), reverse=True)
    # Heuristic 2: value only
    order_val = sorted(range(n), key=lambda i: values[i], reverse=True)

    if heuristic == "weight_deg":
        sel, sc = _run_order(order_deg)
    elif heuristic == "weight":
        sel, sc = _run_order(order_val)
    else:  # "best"
        sel1, sc1 = _run_order(order_deg)
        sel2, sc2 = _run_order(order_val)
        if sc1 >= sc2:
            sel, sc = sel1, sc1
        else:
            sel, sc = sel2, sc2

    runtime_s = max(1e-6, time.perf_counter() - t0)

    return {
        "schema_version": 1,
        "_note": "Greedy heuristic solution",
        "instance": instance.id,
        "solver": "greedy",
        "selected": sel,
        "score": sc,
        "runtime_s": round(runtime_s, 6),
        "seed": seed,
    }

