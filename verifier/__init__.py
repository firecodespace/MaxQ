"""MaxQ Phase 2 — Verifier package.

Ground truth for mission feasibility and cost. This package is READ-ONLY to
any agent being trained or evaluated and to any code path the optimizer or env
controls at runtime (HANDOFF_PHASE2 §2 rule 1 / charter addition 1).

Public surface
--------------
    from verifier import verify, VERIFIER_VERSION

    report = verify(attempt_dict, constraints_profile="crew_default")
    # report["feasible"], report["cost"], report["constraint_report"]
"""

from verifier.core import VERIFIER_VERSION, verify

__all__ = ["verify", "VERIFIER_VERSION"]

