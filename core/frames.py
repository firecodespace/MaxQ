"""Reference frames and time: GMST, TEME <-> ECEF, geodetic <-> ECEF (WGS-84).

Conventions
-----------
* Time is UTC unix seconds (``t_unix``). UT1-UTC (< 0.9 s) is ignored, which is far below the
  km-level accuracy of TLEs.
* SGP4 outputs TEME in **km** and km/s. ECEF/geodetic functions here work in **metres** (names end in
  ``_m``); the TEME<->ECEF rotations are unit-agnostic (output has the same unit as the input).
* TEME -> ECEF is a rotation about z by GMST (IAU-82). Polar motion and the equation of the equinoxes
  are neglected (metres to tens of metres, well below TLE error). Documented approximation.
* All functions accept scalars or numpy arrays (broadcasting on the leading axis).

Owner: Yash (lane: core/frames.py).
"""

from __future__ import annotations

import numpy as np

from core.constants import (
    JD_J2000,
    JD_UNIX_EPOCH,
    OMEGA_E_RAD_S,
    R_E_M,
    SECONDS_PER_DAY,
    WGS84_B_M,
    WGS84_E2,
)

TWO_PI = 2.0 * np.pi


def unix_to_jd(t_unix):
    """UTC unix seconds -> Julian date (UT1 ~ UTC)."""
    return np.asarray(t_unix, dtype=float) / SECONDS_PER_DAY + JD_UNIX_EPOCH


def gmst_rad(t_unix):
    """Greenwich mean sidereal time in radians, [0, 2*pi). IAU-1982 model (Vallado eq. 3-47)."""
    jd = unix_to_jd(t_unix)
    t = (jd - JD_J2000) / 36525.0
    seconds = 67310.54841 + (876600.0 * 3600.0 + 8640184.812866) * t + 0.093104 * t**2 - 6.2e-6 * t**3
    return np.mod(np.deg2rad((seconds % SECONDS_PER_DAY) / 240.0), TWO_PI)


def _rot_z(theta):
    """Rotation matrices about z by theta (rad); shape (...,3,3). Passive: vector components in new frame."""
    c, s = np.cos(theta), np.sin(theta)
    z, o = np.zeros_like(c), np.ones_like(c)
    return np.stack([np.stack([c, s, z], -1), np.stack([-s, c, z], -1), np.stack([z, z, o], -1)], -2)


def teme_to_ecef(r_teme, t_unix, v_teme=None):
    """Rotate TEME position (and optionally velocity) into ECEF (PEF approximation).

    ``r_teme``: (...,3). Returns ``r_ecef`` or ``(r_ecef, v_ecef)``; same units as the input.
    Velocity includes the rotating-frame term: v_ecef = R v - omega x r_ecef.
    """
    r = np.asarray(r_teme, dtype=float)
    rot = _rot_z(gmst_rad(t_unix))
    r_ecef = np.einsum("...ij,...j->...i", rot, r)
    if v_teme is None:
        return r_ecef
    v = np.asarray(v_teme, dtype=float)
    v_rot = np.einsum("...ij,...j->...i", rot, v)
    omega = np.array([0.0, 0.0, OMEGA_E_RAD_S])
    return r_ecef, v_rot - np.cross(omega, r_ecef)


def ecef_to_teme(r_ecef, t_unix, v_ecef=None):
    """Inverse of :func:`teme_to_ecef`."""
    r = np.asarray(r_ecef, dtype=float)
    rot_t = np.swapaxes(_rot_z(gmst_rad(t_unix)), -1, -2)
    r_teme = np.einsum("...ij,...j->...i", rot_t, r)
    if v_ecef is None:
        return r_teme
    omega = np.array([0.0, 0.0, OMEGA_E_RAD_S])
    v = np.asarray(v_ecef, dtype=float) + np.cross(omega, r)
    return r_teme, np.einsum("...ij,...j->...i", rot_t, v)


def geodetic_to_ecef(lat_deg, lon_deg, alt_m):
    """WGS-84 geodetic (deg, deg, m above ellipsoid) -> ECEF metres, shape (...,3)."""
    lat = np.deg2rad(np.asarray(lat_deg, dtype=float))
    lon = np.deg2rad(np.asarray(lon_deg, dtype=float))
    h = np.asarray(alt_m, dtype=float)
    sin_lat, cos_lat = np.sin(lat), np.cos(lat)
    n = R_E_M / np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
    x = (n + h) * cos_lat * np.cos(lon)
    y = (n + h) * cos_lat * np.sin(lon)
    z = (n * (1.0 - WGS84_E2) + h) * sin_lat
    return np.stack(np.broadcast_arrays(x, y, z), axis=-1)


def ecef_to_geodetic(r_ecef_m):
    """ECEF metres (...,3) -> (lat_deg, lon_deg, alt_m) on WGS-84.

    Bowring-style fixed-point iteration on latitude using the exact prime-vertical radius;
    converges to < 1e-9 m for altitudes from below the surface through GEO and beyond.
    """
    r = np.asarray(r_ecef_m, dtype=float)
    x, y, z = r[..., 0], r[..., 1], r[..., 2]
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1.0 - WGS84_E2))  # initial guess
    for _ in range(10):
        sin_lat = np.sin(lat)
        n = R_E_M / np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
        new_lat = np.arctan2(z + WGS84_E2 * n * sin_lat, p)
        done = np.max(np.abs(new_lat - lat)) < 1e-14
        lat = new_lat
        if done:
            break
    sin_lat, cos_lat = np.sin(lat), np.cos(lat)
    n = R_E_M / np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
    # Use whichever formula is better conditioned (p/cos near the equator, z/sin near the poles).
    with np.errstate(divide="ignore", invalid="ignore"):
        alt_eq = p / cos_lat - n
        alt_pole = np.abs(z) / np.abs(sin_lat) - n * (1.0 - WGS84_E2)
    alt = np.where(np.abs(cos_lat) > np.abs(sin_lat), alt_eq, alt_pole)
    return np.rad2deg(lat), np.rad2deg(lon), alt


def teme_km_to_geodetic(r_teme_km, t_unix):
    """Convenience: SGP4 TEME position (km) at ``t_unix`` -> (lat_deg, lon_deg, alt_m)."""
    r_ecef_m = teme_to_ecef(np.asarray(r_teme_km, dtype=float) * 1000.0, t_unix)
    return ecef_to_geodetic(r_ecef_m)


def site_teme_m(lat_deg, lon_deg, alt_m, t_unix):
    """Ground-site position in TEME (the 'ECI' frame used with SGP4 output), metres."""
    return ecef_to_teme(geodetic_to_ecef(lat_deg, lon_deg, alt_m), t_unix)


# Re-exported so callers do not need core.constants for the ellipsoid.
WGS84_SEMI_MAJOR_M = R_E_M
WGS84_SEMI_MINOR_M = WGS84_B_M
