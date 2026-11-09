"""WP-01 acceptance: frames (GMST, TEME<->ECEF, geodetic<->ECEF). Owner: Yash."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from core.constants import OMEGA_E_RAD_S, R_E_M, WGS84_B_M
from core.frames import (
    ecef_to_geodetic,
    ecef_to_teme,
    geodetic_to_ecef,
    gmst_rad,
    teme_km_to_geodetic,
    teme_to_ecef,
)


def test_frames_roundtrip():
    """geodetic -> ECEF -> geodetic error < 1 m, over the whole globe and from below ground to beyond GEO."""
    rng = np.random.default_rng(12345)
    n = 200_000
    lat = np.degrees(np.arcsin(rng.uniform(-1, 1, n)))  # uniform on the sphere
    lon = rng.uniform(-180, 180, n)
    alt = np.concatenate([rng.uniform(-5_000, 100_000, n // 2), rng.uniform(100_000, 50_000_000, n - n // 2)])
    # include exact poles, equator, antimeridian
    lat[:6] = [90, -90, 0, 0, 89.9999999, -89.9999999]
    lon[:6] = [0, 0, 0, 180, 33, -77]
    alt[:6] = [0, 0, 0, 400_000, 35_786_000, 12.5]

    ecef = geodetic_to_ecef(lat, lon, alt)
    lat2, lon2, alt2 = ecef_to_geodetic(ecef)
    back = geodetic_to_ecef(lat2, lon2, alt2)
    assert np.max(np.linalg.norm(back - ecef, axis=-1)) < 1.0  # metres (the actual error is ~1e-8 m)
    assert np.max(np.abs(alt2 - alt)) < 1.0
    dlon = np.abs((lon2 - lon + 180) % 360 - 180)
    away_from_poles = np.abs(lat) < 89.9
    assert np.max(np.abs(lat2 - lat)[away_from_poles]) < 1e-7  # deg ~ 1 cm
    assert np.max(dlon[away_from_poles]) < 1e-7


def test_geodetic_known_points():
    np.testing.assert_allclose(geodetic_to_ecef(0, 0, 0), [R_E_M, 0, 0], atol=1e-6)
    np.testing.assert_allclose(geodetic_to_ecef(0, 90, 0), [0, R_E_M, 0], atol=1e-6)
    np.testing.assert_allclose(geodetic_to_ecef(90, 0, 0), [0, 0, WGS84_B_M], atol=1e-6)
    assert WGS84_B_M == pytest.approx(6356752.314245, abs=1e-5)  # WGS-84 semi-minor axis
    # Cape Canaveral launch complex: |r| between b and a, z positive
    r = geodetic_to_ecef(28.5, -80.6, 0)
    assert WGS84_B_M < np.linalg.norm(r) < R_E_M and r[2] > 0


def test_gmst_vallado_example_3_5():
    """Vallado, Fundamentals of Astrodynamics, ex. 3-5: 1992-08-20 12:14:00 UT1 -> GMST = 152.578787810 deg."""
    t = datetime(1992, 8, 20, 12, 14, 0, tzinfo=UTC).timestamp()
    assert np.degrees(gmst_rad(t)) == pytest.approx(152.578787810, abs=1e-6)


def test_gmst_advances_one_sidereal_day():
    t = 1794286000.0
    sidereal_day = 2 * np.pi / OMEGA_E_RAD_S  # ~86164.1 s
    d = (gmst_rad(t + sidereal_day) - gmst_rad(t) + np.pi) % (2 * np.pi) - np.pi
    assert abs(d) < 2e-5  # rad; OMEGA_E vs the IAU-82 rate differ by ~1e-9 relative


def test_teme_ecef_roundtrip_and_norm():
    rng = np.random.default_rng(1)
    r = rng.normal(size=(500, 3)) * 7000
    v = rng.normal(size=(500, 3)) * 7
    t = 1794286000.0 + rng.uniform(0, 86400 * 3, 500)
    r_e, v_e = teme_to_ecef(r, t, v)
    np.testing.assert_allclose(np.linalg.norm(r_e, axis=1), np.linalg.norm(r, axis=1), rtol=1e-12)
    r2, v2 = ecef_to_teme(r_e, t, v_e)
    np.testing.assert_allclose(r2, r, atol=1e-9)
    np.testing.assert_allclose(v2, v, atol=1e-12)


def test_teme_to_ecef_rotation_direction():
    """A point fixed on Earth (ECEF) must appear to move eastward (+ rotation about +z) in TEME."""
    t = 1794286000.0
    r_ecef = geodetic_to_ecef(0, 0, 0)
    a0 = np.arctan2(*ecef_to_teme(r_ecef, t)[[1, 0]])
    a1 = np.arctan2(*ecef_to_teme(r_ecef, t + 60)[[1, 0]])
    assert (a1 - a0) == pytest.approx(OMEGA_E_RAD_S * 60, rel=1e-3)


def test_velocity_of_fixed_ground_point_is_zero_in_ecef():
    t = 1794286000.0
    r_ecef = geodetic_to_ecef(28.5, -80.6, 0)
    r_teme = ecef_to_teme(r_ecef, t)
    v_teme = np.cross([0, 0, OMEGA_E_RAD_S], r_teme)  # inertial velocity of a ground point
    _, v_ecef = teme_to_ecef(r_teme, t, v_teme)
    np.testing.assert_allclose(v_ecef, 0, atol=1e-9)
    # and its inertial speed is the rotation boost w*R*cos(phi) ~ 408 m/s at the Cape (geocentric lat slightly lower)
    assert 400 < np.linalg.norm(v_teme) < 412


def test_teme_km_to_geodetic_equatorial_point():
    # A point on the equator, 400 km up, at t where we choose TEME so ECEF lon = 10 deg
    t = 1794286000.0
    r_ecef = geodetic_to_ecef(0, 10, 400_000)
    r_teme_km = ecef_to_teme(r_ecef, t) / 1000.0
    lat, lon, alt = teme_km_to_geodetic(r_teme_km, t)
    assert (lat, lon, alt) == pytest.approx((0, 10, 400_000), abs=1e-6)
