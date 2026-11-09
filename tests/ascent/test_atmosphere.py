"""Unit tests for ascent atmospheric density and pressure models."""

import pytest

from ascent.atmosphere import (
    ATMOSPHERE_LIMIT_M,
    P_0,
    RHO_0,
    SCALE_HEIGHT_M,
    density,
    dynamic_pressure,
    pressure,
)


def test_sea_level_values():
    assert density(0.0) == pytest.approx(RHO_0, rel=1e-4)
    assert pressure(0.0) == pytest.approx(P_0, rel=1e-4)


def test_scale_height_decay():
    # At h = H, rho should be rho0 / e
    expected_rho = RHO_0 / 2.718281828459045
    assert density(SCALE_HEIGHT_M) == pytest.approx(expected_rho, rel=1e-3)


def test_upper_atmosphere_cutoff():
    assert density(ATMOSPHERE_LIMIT_M + 1000.0) == 0.0
    assert pressure(ATMOSPHERE_LIMIT_M + 1000.0) == 0.0


def test_dynamic_pressure():
    # q = 0.5 * rho * v^2
    # at sea level with v = 100 m/s: 0.5 * 1.225 * 10000 = 6125 Pa
    q = dynamic_pressure(100.0, 0.0)
    assert q == pytest.approx(6125.0, rel=1e-3)
