"""MaxQ Phase 2 — plan-parameter search.

`fixed_sigma_es` is the placeholder baseline: a non-adaptive Gaussian search, named for what it
actually does. It is exported as `optimize_mission` until the real CMA-ES search replaces it, so
that callers do not have to care which one is current.

Nothing here plans a mission. It searches six ascent parameters with MissionEnv in the loop,
streams contract-valid session.jsonl events, and writes attempt files.
"""

from optimize.fixed_sigma_es import optimize_mission

__all__ = ["optimize_mission"]
