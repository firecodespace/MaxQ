"""The two search algorithms, as pure functions over a scalar objective.

Keeping them here, free of missions and contracts, is what lets the comparison test in
`tests/optimize/test_cma_optimizer.py` run the *real* algorithms against a synthetic function
instead of reimplementing them. If a test could only exercise a copy, it would prove nothing about
what the mission path actually runs.

Both minimise. Both take an evaluation budget rather than a generation count, because the budget
that matters upstream is "how many simulations did this cost".

Owner: Yash (lane: optimize/, by Yash's direction -- see status/yash.md).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

Objective = Callable[[np.ndarray], float]


@dataclass
class SearchResult:
    """What a search found, plus enough trace to show that it adapted (or did not)."""

    x_best: np.ndarray
    f_best: float
    evaluations: int
    #: One entry per generation: sigma and the covariance condition number at that point.
    #: Empty for a search that has neither, which is itself the honest answer.
    generations: list[dict[str, Any]] = field(default_factory=list)


def _clip(x: np.ndarray, lower: Sequence[float], upper: Sequence[float]) -> np.ndarray:
    return np.clip(x, np.asarray(lower, dtype=float), np.asarray(upper, dtype=float))


def fixed_sigma_minimize(
    f: Objective,
    x0: Sequence[float],
    sigma: Sequence[float],
    budget: int,
    seed: int,
    lower: Sequence[float],
    upper: Sequence[float],
    on_eval: Callable[[int, np.ndarray, float], None] | None = None,
) -> SearchResult:
    """The placeholder baseline: Gaussian samples around a running mean, with a constant width.

    `sigma` never changes and there is no covariance, so the search has no way to learn that the
    landscape is narrow in one direction and wide in another. On a rotated, ill-conditioned problem
    it has to take steps small enough for the narrow direction in *every* direction, which is why
    it stalls. That is the point of keeping it: it is the thing real CMA-ES has to beat.

    The first sample is x0 itself, and the mean moves 20% toward any candidate that improves on the
    best. This reproduces the original WP2-05 loop exactly.
    """
    rng = np.random.default_rng(seed)
    x_mean = np.asarray(x0, dtype=float).copy()
    sig = np.asarray(sigma, dtype=float)

    x_best = x_mean.copy()
    f_best = float("inf")

    for i in range(budget):
        candidate = x_mean.copy() if i == 0 else _clip(x_mean + rng.normal(0, sig), lower, upper)
        value = float(f(candidate))
        if on_eval:
            on_eval(i, candidate, value)
        if value < f_best:
            f_best = value
            x_best = candidate.copy()
            x_mean = 0.8 * x_mean + 0.2 * candidate

    return SearchResult(x_best=x_best, f_best=f_best, evaluations=budget, generations=[])


def cma_minimize(
    f: Objective,
    x0: Sequence[float],
    sigma0: float,
    budget: int,
    seed: int,
    lower: Sequence[float],
    upper: Sequence[float],
    on_eval: Callable[[int, np.ndarray, float], None] | None = None,
    on_generation: Callable[[dict[str, Any]], None] | None = None,
    popsize: int | None = None,
) -> SearchResult:
    """Real CMA-ES, from the `cma` package (pinned, cma==4.5.0).

    The difference that matters is that both the step size and the full covariance matrix are
    estimated from the samples that worked, so the search distribution stretches along whatever
    direction the landscape rewards. `SearchResult.generations` records sigma and the covariance
    condition number per generation, so "it adapts" is a measurement rather than a claim.

    The budget is in evaluations. A generation that would run past it is cut short and not told to
    the strategy, because a partial population would bias the update.
    """
    import cma  # imported here so the module is importable without it for the baseline alone

    es = cma.CMAEvolutionStrategy(
        list(np.asarray(x0, dtype=float)),
        sigma0,
        {
            "seed": seed,
            "verbose": -9,
            "verb_log": 0,
            "verb_disp": 0,
            "bounds": [list(lower), list(upper)],
            **({"popsize": popsize} if popsize else {}),
        },
    )

    evals = 0
    x_best = np.asarray(x0, dtype=float).copy()
    f_best = float("inf")
    generations: list[dict[str, Any]] = []

    while evals < budget and not es.stop():
        solutions = es.ask()
        if evals + len(solutions) > budget:
            # Not enough budget for a full generation. Spend what is left, then stop: telling the
            # strategy a truncated population would bias the covariance update.
            for x in solutions[: budget - evals]:
                v = float(f(np.asarray(x)))
                if on_eval:
                    on_eval(evals, np.asarray(x), v)
                evals += 1
                if v < f_best:
                    f_best, x_best = v, np.asarray(x).copy()
            break

        values = []
        for x in solutions:
            v = float(f(np.asarray(x)))
            if on_eval:
                on_eval(evals, np.asarray(x), v)
            evals += 1
            values.append(v)
            if v < f_best:
                f_best, x_best = v, np.asarray(x).copy()

        es.tell(solutions, values)
        gen = {
            "generation": len(generations) + 1,
            "evaluations": evals,
            "sigma": float(es.sigma),
            "cov_condition": float(es.sm.condition_number),
            "f_best": f_best,
        }
        generations.append(gen)
        if on_generation:
            on_generation(gen)

    return SearchResult(
        x_best=x_best, f_best=f_best, evaluations=evals, generations=generations
    )
