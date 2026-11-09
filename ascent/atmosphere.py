"""Atmospheric models for ascent trajectory simulation.

Follows exponential atmospheric density model as specified in HANDOFF.md §7:
rho(h) = rho_0 * exp(-h / H)
where:
  rho_0 = 1.225 kg/m^3 (sea-level density)
  H = 8500.0 m (atmospheric scale height)
"""

from __future__ import annotations

import math

# Sea-level atmospheric density (kg/m^3)
RHO_0: float = 1.225

# Atmospheric scale height (m)
SCALE_HEIGHT_M: float = 8500.0

# Sea-level standard atmospheric pressure (Pa)
P_0: float = 101325.0

# Kármán line / upper atmospheric limit for drag consideration (m)
ATMOSPHERE_LIMIT_M: float = 140000.0


def density(alt_m: float) -> float:
    """Compute atmospheric density at altitude alt_m in meters.

    Returns density in kg/m^3. Clamped to 0 for alt_m >= ATMOSPHERE_LIMIT_M.
    """
    if alt_m < 0.0:
        alt_m = 0.0
    if alt_m >= ATMOSPHERE_LIMIT_M:
        return 0.0
    return RHO_0 * math.exp(-alt_m / SCALE_HEIGHT_M)


def pressure(alt_m: float) -> float:
    """Compute atmospheric pressure at altitude alt_m in meters.

    Returns pressure in Pascals (Pa).
    """
    if alt_m < 0.0:
        alt_m = 0.0
    if alt_m >= ATMOSPHERE_LIMIT_M:
        return 0.0
    return P_0 * math.exp(-alt_m / SCALE_HEIGHT_M)


def dynamic_pressure(speed_mps: float, alt_m: float) -> float:
    """Compute dynamic pressure q = 0.5 * rho * v^2 in Pascals (Pa).

    Parameters
    ----------
    speed_mps : float
        Airspeed relative to atmosphere in m/s.
    alt_m : float
        Altitude above sea level in meters.

    Returns
    -------
    float
        Dynamic pressure in Pascals.
    """
    rho = density(alt_m)
    return 0.5 * rho * speed_mps * speed_mps
