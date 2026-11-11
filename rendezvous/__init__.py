"""MaxQ Phase 2 — Phasing & Rendezvous Physics.

Contains:
- Hohmann transfer calculations
- Hill-Clohessy-Wiltshire (HCW) relative motion dynamics in LVLH
"""

from rendezvous.hcw import propagate_hcw, simulate_approach, solve_hcw_transfer
from rendezvous.phasing import hohmann_transfer, solve_phasing_orbit

__all__ = [
    "hohmann_transfer",
    "propagate_hcw",
    "simulate_approach",
    "solve_hcw_transfer",
    "solve_phasing_orbit",
]
