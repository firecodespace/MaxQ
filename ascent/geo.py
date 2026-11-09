"""Geodesic and coordinate transformation utilities for launch trajectories.

Provides spherical great-circle geodesic propagation and Earth rotation
velocity calculations per HANDOFF.md §7.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

# WGS84 and astronomical constants
# Earth equatorial radius (m) - WGS84
R_EARTH: float = 6378137.0

# Earth rotation angular velocity (rad/s) - IERS numerical standards
OMEGA_EARTH: float = 7.2921159e-5

# Standard Earth gravitational parameter (m^3/s^2) - WGS84 / IAU 1976
MU_EARTH: float = 3.986004418e14

# Standard acceleration due to gravity (m/s^2) - ISO 80000-3
G0: float = 9.80665


def earth_rotation_boost(lat_deg: float) -> float:
    """Compute eastward surface velocity from Earth rotation at given latitude.

    v_rot = omega_E * R_E * cos(lat)

    Parameters
    ----------
    lat_deg : float
        Geodetic latitude in degrees.

    Returns
    -------
    float
        Surface velocity in m/s (approx 408.5 m/s at Cape Canaveral 28.5 deg).
    """
    lat_rad = math.radians(lat_deg)
    return OMEGA_EARTH * R_EARTH * math.cos(lat_rad)


def circular_orbit_speed(alt_m: float) -> float:
    """Compute circular orbital velocity at given altitude.

    v_circ = sqrt(mu / (R_E + alt_m))

    Parameters
    ----------
    alt_m : float
        Altitude above Earth surface in meters.

    Returns
    -------
    float
        Circular speed in m/s (approx 7.78 km/s at 200 km).
    """
    r = R_EARTH + alt_m
    return math.sqrt(MU_EARTH / r)


def launch_azimuth(lat_deg: float, incl_deg: float, ascending: bool = True) -> float:
    """Calculate inertial launch azimuth to reach target orbital inclination.

    sin(beta) = cos(i) / cos(phi)

    Parameters
    ----------
    lat_deg : float
        Launch site latitude in degrees.
    incl_deg : float
        Target orbital inclination in degrees.
    ascending : bool
        True for ascending pass (northeast), False for descending (southeast).

    Returns
    -------
    float
        Inertial azimuth in degrees [0, 360).
    """
    phi = math.radians(lat_deg)
    i = math.radians(incl_deg)
    cos_phi = math.cos(phi)
    if cos_phi == 0.0:
        return 90.0
    sin_beta = math.cos(i) / cos_phi
    sin_beta = max(-1.0, min(1.0, sin_beta))
    beta = math.asin(sin_beta)
    az_deg = math.degrees(beta)
    if not ascending:
        az_deg = 180.0 - az_deg
    return (az_deg + 360.0) % 360.0


def spherical_forward(
    lat0_deg: float,
    lon0_deg: float,
    azimuth_deg: float,
    downrange_m: float,
) -> tuple[float, float]:
    """Propagate coordinates along a spherical great-circle geodesic.

    Given launch point (lat0, lon0), launch azimuth beta, and downrange distance s,
    computes the target geodetic coordinates (lat, lon) on a sphere of radius R_E.

    Parameters
    ----------
    lat0_deg : float
        Initial latitude in degrees.
    lon0_deg : float
        Initial longitude in degrees.
    azimuth_deg : float
        Initial launch azimuth in degrees (clockwise from North).
    downrange_m : float
        Surface downrange distance traveled in meters.

    Returns
    -------
    tuple[float, float]
        (lat_deg, lon_deg) where lat is in [-90, 90] and lon is in [-180, 180].
    """
    lat0 = math.radians(lat0_deg)
    lon0 = math.radians(lon0_deg)
    beta = math.radians(azimuth_deg)
    sigma = downrange_m / R_EARTH  # angular distance in radians

    sin_lat0 = math.sin(lat0)
    cos_lat0 = math.cos(lat0)
    sin_sigma = math.sin(sigma)
    cos_sigma = math.cos(sigma)
    cos_beta = math.cos(beta)
    sin_beta = math.sin(beta)

    # Destination latitude
    sin_lat = sin_lat0 * cos_sigma + cos_lat0 * sin_sigma * cos_beta
    sin_lat = max(-1.0, min(1.0, sin_lat))
    lat = math.asin(sin_lat)

    # Destination longitude delta
    dlon = math.atan2(
        sin_beta * sin_sigma * cos_lat0,
        cos_sigma - sin_lat0 * sin_lat,
    )
    lon = lon0 + dlon

    # Normalize longitude to [-pi, pi]
    lon = (lon + math.pi) % (2.0 * math.pi) - math.pi

    return math.degrees(lat), math.degrees(lon)


def spherical_forward_array(
    lat0_deg: float,
    lon0_deg: float,
    azimuth_deg: float,
    downrange_m_seq: Sequence[float] | np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized spherical great circle propagation for a sequence of downrange points."""
    arr = np.asarray(downrange_m_seq, dtype=np.float64)
    lat0 = math.radians(lat0_deg)
    lon0 = math.radians(lon0_deg)
    beta = math.radians(azimuth_deg)
    sigma = arr / R_EARTH

    sin_lat0 = math.sin(lat0)
    cos_lat0 = math.cos(lat0)
    sin_sigma = np.sin(sigma)
    cos_sigma = np.cos(sigma)
    cos_beta = math.cos(beta)
    sin_beta = math.sin(beta)

    sin_lat = sin_lat0 * cos_sigma + cos_lat0 * sin_sigma * cos_beta
    np.clip(sin_lat, -1.0, 1.0, out=sin_lat)
    lat = np.arcsin(sin_lat)

    dlon = np.arctan2(
        sin_beta * sin_sigma * cos_lat0,
        cos_sigma - sin_lat0 * sin_lat,
    )
    lon = lon0 + dlon
    lon = (lon + np.pi) % (2.0 * np.pi) - np.pi

    return np.degrees(lat), np.degrees(lon)
