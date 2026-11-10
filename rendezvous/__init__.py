"""MaxQ Phase 2 — Phasing & Rendezvous Physics.

Contains:
- Hohmann transfer calculations
- Hill-Clohessy-Wiltshire (HCW) relative motion dynamics in LVLH
"""

from rendezvous.hcw import propagate_hcw
from rendezvous.phasing import hohmann_transfer

__all__ = ["hohmann_transfer", "propagate_hcw"]
