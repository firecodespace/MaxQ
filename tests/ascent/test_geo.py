"""Tests for coordinate transformations, rotation boost, and spherical geodesic."""

import math

import pytest

from ascent.geo import (
    R_EARTH,
    circular_orbit_speed,
    earth_rotation_boost,
    launch_azimuth,
    spherical_forward,
    spherical_forward_array,
)


def test_rotation_boost_cape():
    """HANDOFF.md §7: Earth-rotation boost ~408 m/s at the Cape (405-412)."""
    v_boost = earth_rotation_boost(28.5)
    assert 405.0 <= v_boost <= 412.0
    assert v_boost == pytest.approx(408.5, abs=1.0)


def test_rotation_boost_equator_and_pole():
    v_equator = earth_rotation_boost(0.0)
    assert v_equator == pytest.approx(465.1, abs=0.5)
    v_pole = earth_rotation_boost(90.0)
    assert v_pole == pytest.approx(0.0, abs=1e-5)


def test_circular_speed_200km():
    """HANDOFF.md §7: circular speed at 200 km ≈ 7.78 km/s (± 0.02)."""
    v_circ = circular_orbit_speed(200000.0)
    assert v_circ / 1000.0 == pytest.approx(7.78, abs=0.02)


def test_azimuth_cape_iss():
    """HANDOFF.md §7: Cape 28.5°N, i = 51.64° -> azimuth ≈ 44.9° (accept 44-46°)."""
    az = launch_azimuth(lat_deg=28.5, incl_deg=51.64, ascending=True)
    assert 44.0 <= az <= 46.0
    assert az == pytest.approx(44.9, abs=0.5)


def test_spherical_forward_preserves_start_at_zero():
    lat, lon = spherical_forward(28.5, -80.6, 44.9, 0.0)
    assert lat == pytest.approx(28.5, abs=1e-6)
    assert lon == pytest.approx(-80.6, abs=1e-6)


def test_spherical_forward_quarter_orbit_downrange():
    # Downrange of 1/4 Earth circumference along equator (azimuth 90 deg from 0,0)
    s = (math.pi / 2.0) * R_EARTH
    lat, lon = spherical_forward(0.0, 0.0, 90.0, s)
    assert lat == pytest.approx(0.0, abs=1e-5)
    assert lon == pytest.approx(90.0, abs=1e-5)


def test_spherical_forward_array():
    downrange = [0.0, 100000.0, 500000.0]
    lats, lons = spherical_forward_array(28.5, -80.6, 44.9, downrange)
    assert len(lats) == 3
    assert len(lons) == 3
    assert lats[0] == pytest.approx(28.5, abs=1e-5)
    assert lons[0] == pytest.approx(-80.6, abs=1e-5)
    # Downrange increases latitude for ascending NE launch
    assert lats[1] > lats[0]
    assert lons[1] > lons[0]
