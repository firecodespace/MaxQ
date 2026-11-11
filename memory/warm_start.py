"""Warm-start the search from retrieved experience (WP2-10, HANDOFF section 9).

Memory is a HINT, never a constraint and never evidence (charter addition 3). Everything here
produces a starting point or a narrowed box for CMA-ES; the verifier still judges every
attempt from scratch, and a warm start that leads somewhere infeasible costs attempts and
nothing else. Nothing in this module can make an infeasible plan pass.

The three strategies HANDOFF section 9 asks for
-----------------------------------------------
1. **centre** -- start CMA-ES at the best feasible neighbour's plan. The cheapest thing that
   could work: if a similar mission's plan was good, the search begins near it instead of at
   a hand-written default.
2. **bound** -- in addition, shrink the search box to the spread of the retrieved neighbours
   (widened by a margin). Faster when the neighbours bracket the answer, actively harmful
   when they do not, which is why it is not the default and why `margin` exists.
3. **examples** -- return the neighbours as structured cases for an LLM planner to read
   (WP2-11). No model exists yet, so this returns the cases and nothing consumes them.

Why "centre" is the default: bounding trades robustness for speed, and a benchmark is
supposed to measure the search, not our luck in picking a box. The comparison that justifies
any of this is `cma_cold` vs `cma_memory` on a held-out split -- until that has run, the
honest claim about warm starting is that it is implemented, not that it helps.

Owner: Yash (lane: memory/).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from memory.store import Store

STRATEGIES = ("cold", "centre", "bound", "examples")

DEFAULT_K = 5

#: How far outside the neighbours' spread the bounded box may still reach, as a fraction of
#: that spread. Without it, a box drawn tight around five neighbours excludes anything better
#: than all five, which is the opposite of searching.
DEFAULT_MARGIN = 0.5


def retrieve(
    store: Store,
    mission_request: dict[str, Any],
    *,
    k: int = DEFAULT_K,
    sim_version: str | None = None,
    verifier_version: str | None = None,
    feasible_only: bool = True,
) -> list[dict[str, Any]]:
    """Neighbours for this mission, filtered to the current physics.

    `sim_version` defaults to no filter only because the caller may not know it yet; the
    optimizer passes it, and `benchmarks/runner.py` requires it. A neighbour from a different
    simulator is a hint about different physics.
    """
    return store.search(
        mission_request,
        k=k,
        sim_version=sim_version,
        verifier_version=verifier_version,
        profile=mission_request["profile"],
        rocket_id=mission_request["rocket_id"],
        feasible=True if feasible_only else None,
    )


def warm_start(
    store: Store,
    mission_request: dict[str, Any],
    *,
    x_from_plan: Callable[[dict[str, Any]], np.ndarray | None],
    x0: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    strategy: str = "centre",
    k: int = DEFAULT_K,
    margin: float = DEFAULT_MARGIN,
    sim_version: str | None = None,
    verifier_version: str | None = None,
) -> dict[str, Any]:
    """Compute a starting point and bounds for CMA-ES, plus a trace of what was used.

    Always returns a usable (x0, lower, upper): with no memory, or no usable neighbour, it
    hands back exactly what it was given and says so in `reason`. A cold start is the correct
    behaviour for an empty memory, not an error.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown warm-start strategy {strategy!r}; known: {STRATEGIES}")

    out: dict[str, Any] = {
        "strategy": strategy,
        "x0": x0.copy(),
        "lower": lower.copy(),
        "upper": upper.copy(),
        "neighbours": [],
        "used": False,
        "reason": "",
    }
    if strategy == "cold":
        out["reason"] = "cold start requested"
        return out

    hits = retrieve(
        store,
        mission_request,
        k=k,
        sim_version=sim_version,
        verifier_version=verifier_version,
    )
    out["neighbours"] = [
        {
            "attempt_id": h["record"]["payload"]["attempt_id"],
            "mission_id": h["record"]["payload"]["mission_id"],
            "similarity": h["similarity"],
            "cost": h["record"]["payload"]["cost"],
            "feasible": h["record"]["payload"]["feasible"],
        }
        for h in hits
    ]
    if not hits:
        out["reason"] = "no feasible neighbour in memory for this profile and vehicle"
        return out

    vectors: list[np.ndarray] = []
    costs: list[float] = []
    for h in hits:
        v = x_from_plan(h["record"]["payload"]["plan"])
        if v is None or v.shape != x0.shape:
            continue
        vectors.append(np.clip(v, lower, upper))
        costs.append(float(h["record"]["payload"]["cost"]))
    if not vectors:
        out["reason"] = (
            f"{len(hits)} neighbour(s) retrieved but none carried a complete, same-shaped "
            f"plan -- most likely stored under a different parameterisation"
        )
        return out

    best = vectors[int(np.argmin(costs))]
    out["x0"] = best
    out["used"] = True
    out["reason"] = (
        f"centred on the best of {len(vectors)} feasible neighbour(s), cost "
        f"{min(costs):.6f}, similarity {hits[int(np.argmin(costs))]['similarity']:.4f}"
    )

    if strategy == "bound":
        stack = np.vstack(vectors)
        lo, hi = stack.min(axis=0), stack.max(axis=0)
        spread = np.maximum(hi - lo, (upper - lower) * 1e-3)
        out["lower"] = np.maximum(lower, lo - margin * spread)
        out["upper"] = np.minimum(upper, hi + margin * spread)
        out["reason"] += (
            f"; box narrowed to the neighbours' spread widened by {margin:.0%} "
            f"(mean width {float(np.mean(out['upper'] - out['lower'])):.4g} against "
            f"{float(np.mean(upper - lower)):.4g} cold)"
        )

    if strategy == "examples":
        out["examples"] = [
            {
                "mission_id": h["record"]["payload"]["mission_id"],
                "similarity": h["similarity"],
                "plan": h["record"]["payload"]["plan"],
                "cost": h["record"]["payload"]["cost"],
                "feasible": h["record"]["payload"]["feasible"],
                "failure_modes": h["record"]["payload"]["failure_modes"],
            }
            for h in hits
        ]
        out["reason"] += f"; {len(hits)} case(s) returned for a planner to read"

    return out
