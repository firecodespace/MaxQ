"""Unit tests for SGP4 orbital residual modeling and RIC transformations (WP-15)."""

from __future__ import annotations

import numpy as np
import pytest

from catalog.residuals import (
    Sgp4ResidualModel,
    load_historical_iss_sequence,
    ric_to_teme,
    run_residuals_benchmark,
    teme_to_ric,
    teme_to_ric_matrix,
)


def test_teme_to_ric_matrix_orthonormality():
    """Verify RIC rotation matrix is strictly orthonormal with det(R) = +1."""
    # Standard LEO circular orbit state: r along X, v along Y
    r = np.array([6800.0, 0.0, 0.0])
    v = np.array([0.0, 7.6, 0.0])

    r_mat = teme_to_ric_matrix(r, v)
    assert r_mat.shape == (3, 3)

    # Orthonormality: R @ R.T = I
    identity = np.eye(3)
    assert np.allclose(r_mat @ r_mat.T, identity, atol=1e-12)
    assert np.allclose(r_mat.T @ r_mat, identity, atol=1e-12)

    # Right-handed coordinate system: det(R) = +1.0
    assert abs(np.linalg.det(r_mat) - 1.0) < 1e-12

    # Radial vector points along r
    r_hat = r / np.linalg.norm(r)
    assert np.allclose(r_mat[0], r_hat, atol=1e-12)

    # Cross-track points along angular momentum h = r x v
    h = np.cross(r, v)
    c_hat = h / np.linalg.norm(h)
    assert np.allclose(r_mat[2], c_hat, atol=1e-12)

    # In-track completes the right-handed triad
    assert np.allclose(r_mat[1], np.cross(c_hat, r_hat), atol=1e-12)


def test_teme_to_ric_and_back_roundtrip():
    """Verify displacement vectors transform to RIC and back to TEME with machine precision."""
    rng = np.random.default_rng(42)
    n = 50
    # Random orbital positions and velocities
    r_teme = rng.normal(size=(n, 3)) * 6800.0
    v_teme = rng.normal(size=(n, 3)) * 7.5

    # Random displacement residuals (e.g. 1 to 50 km)
    delta_teme = rng.normal(size=(n, 3)) * 25.0

    delta_ric = teme_to_ric(delta_teme, r_teme, v_teme)
    assert delta_ric.shape == (n, 3)

    # Invert back to TEME
    recovered_teme = ric_to_teme(delta_ric, r_teme, v_teme)
    assert np.allclose(recovered_teme, delta_teme, atol=1e-10)

    # Norm must be invariant under rotation
    norm_teme = np.linalg.norm(delta_teme, axis=-1)
    norm_ric = np.linalg.norm(delta_ric, axis=-1)
    assert np.allclose(norm_teme, norm_ric, atol=1e-10)


def test_single_vector_conversion():
    """Verify 1D single-vector interface for RIC transformations."""
    r = np.array([5000.0, 3000.0, 2000.0])
    v = np.array([-2.0, 5.0, 4.0])
    delta = np.array([1.5, -2.5, 0.8])

    ric = teme_to_ric(delta, r, v)
    assert ric.shape == (3,)

    back = ric_to_teme(ric, r, v)
    assert back.shape == (3,)
    assert np.allclose(back, delta, atol=1e-12)


def test_residual_model_lifecycle():
    """Verify Sgp4ResidualModel fit, prediction, and state validation."""
    t0 = 1794286000.0
    model = Sgp4ResidualModel(t0_unix=t0)

    # Predict before fit raises error
    with pytest.raises(RuntimeError, match="must be fitted"):
        model.predict_ric(np.array([t0 + 100.0]))

    # Synthetic calibration data
    times = np.linspace(t0, t0 + 2 * 86400, 100)
    dt_days = (times - t0) / 86400.0
    # Simulate quadratic in-track drag drift (-5 km/day^2) and small periodic radial oscillations
    res_ric = np.zeros((100, 3))
    res_ric[:, 1] = -5.0 * (dt_days**2) - 0.5 * dt_days
    res_ric[:, 0] = 0.8 * np.sin(2.0 * np.pi * dt_days * 15.5)

    model.fit(times, res_ric)
    assert model.is_fitted

    # Predict on future times
    eval_times = np.linspace(t0 + 2 * 86400, t0 + 5 * 86400, 50)
    pred_ric = model.predict_ric(eval_times)

    assert pred_ric.shape == (50, 3)
    assert np.isfinite(pred_ric).all()
    # In-track drift should remain negative and grow quadratically
    assert pred_ric[-1, 1] < pred_ric[0, 1]


def test_load_historical_iss_sequence():
    """Verify loading and initialization of historical ISS TLE sequence."""
    sats = load_historical_iss_sequence()
    assert len(sats) >= 10
    for s in sats:
        assert s.satnum == 25544


def test_end_to_end_residual_reduction():
    """Verify SGP4 residual correction reduces holdout error by at least 50%."""
    metrics = run_residuals_benchmark(horizon_days=7.0, calibration_days=2.0, time_step_s=120.0)

    assert metrics["norad_id"] == 25544
    assert metrics["rms_uncorrected_km"] > 50.0  # Raw SGP4 accumulates significant error over 7 days
    assert metrics["rms_corrected_km"] < metrics["rms_uncorrected_km"]
    assert metrics["error_reduction_pct"] >= 50.0  # High reduction (>50%)
    assert metrics["intrack_rms_corrected_km"] < metrics["intrack_rms_uncorrected_km"]

