"""MaxQ manifest solver registry and dispatcher."""

from __future__ import annotations

from typing import Any

from manifest.instance import ManifestInstance
from manifest.solvers.cpsat import solve_cpsat
from manifest.solvers.greedy import solve_greedy
from manifest.solvers.qaoa import solve_qaoa
from manifest.solvers.sa import solve_sa

SOLVERS = {
    "greedy": solve_greedy,
    "sa": solve_sa,
    "cpsat": solve_cpsat,
    "qaoa": solve_qaoa,
}


def solve_manifest(
    instance: ManifestInstance,
    solver: str = "cpsat",
    seed: int = 42,
    **kwargs: Any,
) -> dict[str, Any]:
    """Solve a manifest instance using the requested solver.

    Args:
        instance: ManifestInstance to solve.
        solver: 'greedy', 'sa', or 'cpsat'.
        seed: Random seed.
        **kwargs: Additional solver-specific options.

    Returns:
        Contract-compliant dictionary with schema_version, instance, solver,
        selected, score, runtime_s, seed.
    """
    solver_key = solver.lower()
    if solver_key not in SOLVERS:
        raise ValueError(f"Unknown solver {solver!r}. Available: {list(SOLVERS.keys())}")
    return SOLVERS[solver_key](instance, seed=seed, **kwargs)


__all__ = ["solve_manifest", "solve_greedy", "solve_sa", "solve_cpsat", "solve_qaoa", "SOLVERS"]

