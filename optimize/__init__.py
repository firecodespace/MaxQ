"""MaxQ Phase 2 — Baseline Optimizer Package.

Runs CMA-ES search over plan parameters with MissionEnv in the loop,
streams contract-valid session.jsonl events, and exports attempt files.
"""

from optimize.cma_optimizer import optimize_mission

__all__ = ["optimize_mission"]
