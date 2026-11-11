"""MaxQ Phase 2 — plan-parameter search.

`cma_optimizer` is the current search: real CMA-ES from the pinned `cma` package. `fixed_sigma_es`
is the non-adaptive baseline it replaced, kept so the comparison test in
`tests/optimize/test_cma_optimizer.py` can measure the real algorithms against each other rather
than against reimplementations. `optimize_mission` is the current one, so callers do not have to
care which that is.

Nothing here plans a mission. It searches six ascent parameters with MissionEnv in the loop,
streams contract-valid session.jsonl events, and writes attempt files.
"""

from optimize.cma_optimizer import optimize_mission

__all__ = ["optimize_mission"]
