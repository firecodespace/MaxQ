"""Hill-Clohessy-Wiltshire (HCW) equations of relative motion in LVLH frame."""

from __future__ import annotations

import math

import numpy as np

from ascent.geo import MU_EARTH, R_EARTH


def hcw_state_transition_matrix(n: float, t: float) -> np.ndarray:
    """Compute 6x6 state transition matrix Phi(t) for HCW relative motion.

    LVLH frame:
      x : radial (outward from Earth)
      y : in-track (along-orbit in direction of motion)
      z : cross-track (normal to orbital plane)
    """
    nt = n * t
    s = math.sin(nt)
    c = math.cos(nt)

    phi = np.zeros((6, 6), dtype=float)

    # Position from position: Phi_rr
    phi[0, 0] = 4.0 - 3.0 * c
    phi[0, 1] = 0.0
    phi[0, 2] = 0.0

    phi[1, 0] = 6.0 * (s - nt)
    phi[1, 1] = 1.0
    phi[1, 2] = 0.0

    phi[2, 0] = 0.0
    phi[2, 1] = 0.0
    phi[2, 2] = c

    # Position from velocity: Phi_rv
    phi[0, 3] = s / n
    phi[0, 4] = 2.0 * (1.0 - c) / n
    phi[0, 5] = 0.0

    phi[1, 3] = -2.0 * (1.0 - c) / n
    phi[1, 4] = (4.0 * s - 3.0 * nt) / n
    phi[1, 5] = 0.0

    phi[2, 3] = 0.0
    phi[2, 4] = 0.0
    phi[2, 5] = s / n

    # Velocity from position: Phi_vr
    phi[3, 0] = 3.0 * n * s
    phi[3, 1] = 0.0
    phi[3, 2] = 0.0

    phi[4, 0] = -6.0 * n * (1.0 - c)
    phi[4, 1] = 0.0
    phi[4, 2] = 0.0

    phi[5, 0] = 0.0
    phi[5, 1] = 0.0
    phi[5, 2] = -n * s

    # Velocity from velocity: Phi_vv
    phi[3, 3] = c
    phi[3, 4] = 2.0 * s
    phi[3, 5] = 0.0

    phi[4, 3] = -2.0 * s
    phi[4, 4] = 4.0 * c - 3.0
    phi[4, 5] = 0.0

    phi[5, 3] = 0.0
    phi[5, 4] = 0.0
    phi[5, 5] = c

    return phi


def propagate_hcw(
    r0_m: np.ndarray,
    v0_mps: np.ndarray,
    t_s: float,
    target_alt_km: float = 410.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Propagate relative state in LVLH frame using HCW dynamics."""
    r_target = R_EARTH + target_alt_km * 1000.0
    n = math.sqrt(MU_EARTH / (r_target**3))

    state0 = np.concatenate([r0_m, v0_mps])
    phi = hcw_state_transition_matrix(n, t_s)
    state_t = phi @ state0

    return state_t[:3], state_t[3:]
