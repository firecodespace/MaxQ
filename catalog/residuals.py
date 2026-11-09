# ruff: noqa: E402
"""SGP4 orbital residual modeling and position correction (WP-15).

Decomposes SGP4 ephemeris errors in the satellite-local Radial, In-track (along-track),
and Cross-track (RIC) coordinate frame.
Fits ballistic secular drift (polynomial in-track drag) and periodic harmonic oscillations
(radial / cross-track geopotential residuals) to reduce 7-day SGP4 propagation errors.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())
import matplotlib.pyplot as plt
import numpy as np
from sgp4.api import Satrec
from sgp4 import omm as sgp4_omm
from sklearn.linear_model import Ridge

from catalog.omm import satrec_from_row
from catalog.propagate import propagate_teme, sat_epoch_unix

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_HISTORICAL_PATH = REPO_ROOT / "data" / "sample" / "historical_tles_25544.json"
RESULTS_DIR = REPO_ROOT / "data" / "results"


def teme_to_ric_matrix(r_teme: np.ndarray, v_teme: np.ndarray) -> np.ndarray:
    """Compute 3x3 orthonormal rotation matrices from TEME to satellite RIC frame.

    R-hat = r / ||r|| (Radial)
    C-hat = (r x v) / ||r x v|| (Cross-track, orbital momentum direction)
    I-hat = C-hat x R-hat (In-track / along-track, completes right-handed system)

    Supports (3,) or (N, 3) arrays. Returns (..., 3, 3) matrix where:
        v_ric = R_mat @ v_teme
    """
    r = np.atleast_2d(r_teme)
    v = np.atleast_2d(v_teme)

    r_norm = np.linalg.norm(r, axis=-1, keepdims=True)
    r_hat = r / np.maximum(r_norm, 1e-12)

    h_vec = np.cross(r, v)
    h_norm = np.linalg.norm(h_vec, axis=-1, keepdims=True)
    c_hat = h_vec / np.maximum(h_norm, 1e-12)

    i_hat = np.cross(c_hat, r_hat)

    # Stack along rows: row 0 is R, row 1 is I, row 2 is C
    rot = np.stack([r_hat, i_hat, c_hat], axis=-2)
    if r_teme.ndim == 1:
        return rot[0]
    return rot


def teme_to_ric(delta_r_teme: np.ndarray, r_teme: np.ndarray, v_teme: np.ndarray) -> np.ndarray:
    """Transform displacement vector delta_r from TEME to RIC frame."""
    rot = teme_to_ric_matrix(r_teme, v_teme)
    if delta_r_teme.ndim == 1:
        return rot @ delta_r_teme
    # Batched matrix-vector multiplication: (N, 3, 3) x (N, 3, 1) -> (N, 3)
    return np.einsum("nij,nj->ni", rot, delta_r_teme)


def ric_to_teme(delta_r_ric: np.ndarray, r_teme: np.ndarray, v_teme: np.ndarray) -> np.ndarray:
    """Transform displacement vector delta_r from RIC frame back to TEME."""
    rot = teme_to_ric_matrix(r_teme, v_teme)
    if delta_r_ric.ndim == 1:
        return rot.T @ delta_r_ric
    # Inverse rotation is transpose: R.T @ delta_r_ric
    return np.einsum("nji,nj->ni", rot, delta_r_ric)


class Sgp4ResidualModel:
    """Physics-informed regression model for SGP4 orbital propagation residuals."""

    def __init__(self, t0_unix: float, orbital_period_s: float = 5560.0, alpha: float = 1e-2):
        self.t0_unix = float(t0_unix)
        self.period_s = float(orbital_period_s)
        self.omega = 2.0 * np.pi / self.period_s
        self.alpha = float(alpha)
        self.model_r = Ridge(alpha=self.alpha)
        self.model_i = Ridge(alpha=self.alpha)
        self.model_c = Ridge(alpha=self.alpha)
        self.is_fitted = False

    def _build_features(self, t_unix: np.ndarray) -> np.ndarray:
        dt_days = (np.asarray(t_unix, dtype=float) - self.t0_unix) / 86400.0
        phase = self.omega * (np.asarray(t_unix, dtype=float) - self.t0_unix)

        feats = np.column_stack([
            dt_days,
            dt_days**2,
            np.cos(phase),
            np.sin(phase),
            np.cos(2.0 * phase),
            np.sin(2.0 * phase),
        ])
        return feats

    def fit(self, t_unix: np.ndarray, residuals_ric: np.ndarray) -> Sgp4ResidualModel:
        """Fit radial, in-track, and cross-track models from calibration residuals (km)."""
        x_mat = self._build_features(t_unix)
        self.model_r.fit(x_mat, residuals_ric[:, 0])
        self.model_i.fit(x_mat, residuals_ric[:, 1])
        self.model_c.fit(x_mat, residuals_ric[:, 2])
        self.is_fitted = True
        return self

    def predict_ric(self, t_unix: np.ndarray) -> np.ndarray:
        """Predict expected residual (delta_R, delta_I, delta_C) in km at times t_unix."""
        if not self.is_fitted:
            raise RuntimeError("Sgp4ResidualModel must be fitted before predict.")
        x_mat = self._build_features(t_unix)
        pred_r = self.model_r.predict(x_mat)
        pred_i = self.model_i.predict(x_mat)
        pred_c = self.model_c.predict(x_mat)
        return np.column_stack([pred_r, pred_i, pred_c])

    def correct_position(
        self,
        r_teme: np.ndarray,
        v_teme: np.ndarray,
        t_unix: np.ndarray,
    ) -> np.ndarray:
        """Apply residual correction to SGP4 position vector: r_corr = r_sgp4 + delta_r."""
        ric_pred = self.predict_ric(t_unix)
        delta_teme = ric_to_teme(ric_pred, r_teme, v_teme)
        return r_teme + delta_teme


def load_historical_iss_sequence(path: str | Path = SAMPLE_HISTORICAL_PATH) -> list[Satrec]:
    """Load historical ISS TLE sequence from sample JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    snapshots = data["snapshots"]
    sats: list[Satrec] = []
    for s in snapshots:
        sat = Satrec()
        sgp4_omm.initialize(sat, s)
        sats.append(sat)
    return sats


def run_residuals_benchmark(
    horizon_days: float = 7.0,
    calibration_days: float = 2.0,
    time_step_s: float = 60.0,
) -> dict[str, Any]:
    """Run end-to-end SGP4 residual benchmark across a 7-day propagation horizon."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    sats = load_historical_iss_sequence()

    sat_baseline = sats[0]
    t0_unix = sat_epoch_unix(sat_baseline)
    t_end_unix = t0_unix + horizon_days * 86400.0

    times = np.arange(t0_unix, t_end_unix + 1.0, time_step_s)
    t_calib_end = t0_unix + calibration_days * 86400.0

    # Truth position: evaluated from the most contemporary TLE snapshot at each time step
    # (Matches real operations where tracking updates supersede older elements)
    r_truth_list = []
    r_base_list = []
    v_base_list = []

    # Divide evaluation into 12-hour segments corresponding to TLE updates
    for t_step in times:
        # Find latest snapshot whose epoch <= t_step
        active_sat = sats[0]
        for s in sats:
            if sat_epoch_unix(s) <= t_step:
                active_sat = s
            else:
                break
        r_t, _ = propagate_teme(active_sat, t_step)
        r_b, v_b = propagate_teme(sat_baseline, t_step)

        r_truth_list.append(r_t[0])
        r_base_list.append(r_b[0])
        v_base_list.append(v_b[0])

    r_truth = np.array(r_truth_list)
    r_base = np.array(r_base_list)
    v_base = np.array(v_base_list)

    # Residuals: truth - baseline
    delta_r_teme = r_truth - r_base
    residuals_ric = teme_to_ric(delta_r_teme, r_base, v_base)

    # Split into calibration and holdout evaluation sets
    calib_mask = times <= t_calib_end
    eval_mask = times > t_calib_end

    t_calib = times[calib_mask]
    res_calib = residuals_ric[calib_mask]

    t_eval = times[eval_mask]
    res_eval = residuals_ric[eval_mask]
    r_base_eval = r_base[eval_mask]
    v_base_eval = v_base[eval_mask]
    r_truth_eval = r_truth[eval_mask]

    # Fit residual model on calibration window (first 48 hours)
    model = Sgp4ResidualModel(t0_unix=t0_unix, orbital_period_s=5560.0)
    model.fit(t_calib, res_calib)

    # Correct position on holdout evaluation window (days 2 to 7)
    r_corrected_eval = model.correct_position(r_base_eval, v_base_eval, t_eval)

    # Error statistics on holdout test window
    err_uncorrected = np.linalg.norm(r_truth_eval - r_base_eval, axis=-1)
    err_corrected = np.linalg.norm(r_truth_eval - r_corrected_eval, axis=-1)

    rms_uncorr = float(np.sqrt(np.mean(err_uncorrected**2)))
    max_uncorr = float(np.max(err_uncorrected))

    rms_corr = float(np.sqrt(np.mean(err_corrected**2)))
    max_corr = float(np.max(err_corrected))

    reduction_pct = (1.0 - rms_corr / rms_uncorr) * 100.0

    # Along-track specific error reduction
    pred_ric_eval = model.predict_ric(t_eval)
    intrack_err_uncorr = float(np.sqrt(np.mean(res_eval[:, 1]**2)))
    intrack_err_corr = float(np.sqrt(np.mean((res_eval[:, 1] - pred_ric_eval[:, 1])**2)))

    # Generate benchmark plot
    plot_file = plot_residuals_results(
        times, residuals_ric, t_eval, err_uncorrected, err_corrected, t0_unix, t_calib_end
    )

    metrics = {
        "norad_id": 25544,
        "object_name": "ISS (ZARYA)",
        "horizon_days": horizon_days,
        "calibration_days": calibration_days,
        "rms_uncorrected_km": round(rms_uncorr, 2),
        "max_uncorrected_km": round(max_uncorr, 2),
        "rms_corrected_km": round(rms_corr, 2),
        "max_corrected_km": round(max_corr, 2),
        "error_reduction_pct": round(reduction_pct, 1),
        "intrack_rms_uncorrected_km": round(intrack_err_uncorr, 2),
        "intrack_rms_corrected_km": round(intrack_err_corr, 2),
        "plot_path": str(plot_file),
    }

    with open(RESULTS_DIR / "sgp4_residuals.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    return metrics


def plot_residuals_results(
    times: np.ndarray,
    residuals_ric: np.ndarray,
    t_eval: np.ndarray,
    err_uncorr: np.ndarray,
    err_corr: np.ndarray,
    t0_unix: float,
    t_calib_end: float,
) -> Path:
    """Generate 3-panel visualization: RIC decomposition, error progression, and holdout comparison."""
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16, 4.8), dpi=150)
    fig.patch.set_facecolor("#0b0f19")

    t_days = (times - t0_unix) / 86400.0
    t_eval_days = (t_eval - t0_unix) / 86400.0
    calib_days = (t_calib_end - t0_unix) / 86400.0

    # Panel 1: SGP4 Residuals in RIC Frame
    ax1.plot(t_days, residuals_ric[:, 1], color="#ef4444", linewidth=1.8, label="In-Track (Along-Track)")
    ax1.plot(t_days, residuals_ric[:, 0], color="#38bdf8", linewidth=1.5, label="Radial")
    ax1.plot(t_days, residuals_ric[:, 2], color="#22c55e", linewidth=1.5, label="Cross-Track")
    ax1.axvline(calib_days, color="#9ca3af", linestyle="--", linewidth=1.2, label=f"Calib Split ({calib_days:.1f}d)")

    ax1.set_title("SGP4 Errors in Satellite RIC Frame", color="#f9fafb", fontsize=12, fontweight="bold")
    ax1.set_xlabel("Propagation Elapsed Time (Days)", color="#e5e7eb", fontsize=10)
    ax1.set_ylabel("Position Residual (km)", color="#e5e7eb", fontsize=10)
    ax1.legend(facecolor="#1f2937", edgecolor="#374151", labelcolor="#e5e7eb", fontsize=9, loc="upper left")

    # Panel 2: Uncorrected vs Corrected Error Progression
    ax2.plot(t_eval_days, err_uncorr, color="#ef4444", linewidth=2.0, label="Uncorrected SGP4 Error")
    ax2.plot(t_eval_days, err_corr, color="#22c55e", linewidth=2.0, label="Residual Corrected Error")
    ax2.set_title("7-Day Holdout Error Progression", color="#f9fafb", fontsize=12, fontweight="bold")
    ax2.set_xlabel("Elapsed Time (Days)", color="#e5e7eb", fontsize=10)
    ax2.set_ylabel("Total 3D Position Error (km)", color="#e5e7eb", fontsize=10)
    ax2.legend(facecolor="#1f2937", edgecolor="#374151", labelcolor="#e5e7eb", fontsize=9, loc="upper left")

    # Panel 3: Error Distribution Comparison
    labels = ["Uncorrected SGP4", "Corrected SGP4"]
    rms_vals = [float(np.sqrt(np.mean(err_uncorr**2))), float(np.sqrt(np.mean(err_corr**2)))]
    max_vals = [float(np.max(err_uncorr)), float(np.max(err_corr))]

    x = np.arange(len(labels))
    width = 0.35
    ax3.bar(x - width / 2, rms_vals, width, label="RMS Error (km)", color="#38bdf8", alpha=0.9)
    ax3.bar(x + width / 2, max_vals, width, label="Peak Error (km)", color="#f59e0b", alpha=0.9)

    ax3.set_title("RMS & Peak Error Reduction", color="#f9fafb", fontsize=12, fontweight="bold")
    ax3.set_ylabel("Distance Error (km)", color="#e5e7eb", fontsize=10)
    ax3.set_xticks(x)
    ax3.set_xticklabels(labels)
    ax3.legend(facecolor="#1f2937", edgecolor="#374151", labelcolor="#e5e7eb", fontsize=9)

    # Style axes
    for ax in (ax1, ax2, ax3):
        ax.set_facecolor("#111827")
        ax.tick_params(colors="#9ca3af", which="both")
        for spine in ax.spines.values():
            spine.set_color("#374151")
        ax.grid(True, linestyle="--", alpha=0.3, color="#4b5563")

    plt.suptitle("MaxQ SGP4 Orbital Residual Correction Benchmark (WP-15)", color="#f9fafb", fontsize=14, y=1.02, fontweight="bold")
    plt.tight_layout()

    out_file = RESULTS_DIR / "sgp4_residuals_benchmark.png"
    plt.savefig(out_file, dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return out_file


def main() -> int:
    t0 = time.perf_counter()
    print("=" * 88)
    print("MaxQ SGP4 Orbital Residual Correction (WP-15)")
    print("=" * 88)
    res = run_residuals_benchmark()

    print(f"Target: {res['object_name']} (NORAD {res['norad_id']})")
    print(f"Calibration Window: {res['calibration_days']:.1f} days | Holdout Evaluation: {res['horizon_days'] - res['calibration_days']:.1f} days")
    print(f"Uncorrected SGP4 Error: RMS = {res['rms_uncorrected_km']:.2f} km, Max = {res['max_uncorrected_km']:.2f} km")
    print(f"Corrected SGP4 Error:   RMS = {res['rms_corrected_km']:.2f} km, Max = {res['max_corrected_km']:.2f} km")
    print(f"Position Error Reduction: {res['error_reduction_pct']:.1f}%")
    print(f"In-Track Drag Error: {res['intrack_rms_uncorrected_km']:.2f} km -> {res['intrack_rms_corrected_km']:.2f} km")
    print(f"Benchmark plot saved to: {res['plot_path']}")
    print(f"Completed in {time.perf_counter() - t0:.2f} s.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
