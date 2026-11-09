"""WP-15 tests. Owner: Yash.

IMPORTANT: histories built in this file are SYNTHETIC test fixtures (generated here, drift known by construction).
They test the plumbing (frames, pair building, split, regression, refusal of non-real data). They say NOTHING about real
SGP4 accuracy; real numbers come only from a Space-Track history (see scripts/fetch_spacetrack_history.py).
Nothing here writes into data/results.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
import pytest

from catalog.residuals import (
    HistoryError,
    Pairs,
    ResidualModel,
    arg_of_latitude,
    build_pairs,
    detect_jumps,
    evaluate,
    load_history,
    main,
    ric_to_teme,
    split_pairs,
    teme_to_ric,
    teme_to_ric_matrix,
)

# A real ISS element set (CelesTrak OMM, 2026-11-09) used only as the starting point of the synthetic fixtures.
BASE = {
    "OBJECT_NAME": "ISS (ZARYA)", "OBJECT_ID": "1998-067A", "EPOCH": "2026-11-09T11:10:18.771456",
    "MEAN_MOTION": 15.49275323, "ECCENTRICITY": 0.00072858, "INCLINATION": 51.6321, "RA_OF_ASC_NODE": 301.1765,
    "ARG_OF_PERICENTER": 6.3021, "MEAN_ANOMALY": 353.8058, "EPHEMERIS_TYPE": 0, "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 25544, "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 59427, "BSTAR": 0.00016254709,
    "MEAN_MOTION_DOT": 8.605e-05, "MEAN_MOTION_DDOT": 0,
}  # fmt: skip


def synthetic_history(n=40, seed=1, jump_at=None, source="SYNTHETIC"):
    """n snapshots at irregular spacing with a decaying orbit (known drift) plus small fit noise."""
    rng = np.random.default_rng(seed)
    t0 = datetime.fromisoformat(BASE["EPOCH"])
    dts = np.concatenate([[0.0], np.cumsum(rng.uniform(0.3, 0.7, n - 1))])  # days
    c = 1.0e-4  # rev/day^2 decay of the mean motion
    snaps = []
    for k, dt in enumerate(dts):
        n_rev = BASE["MEAN_MOTION"] + c * dt - (0.003 if jump_at is not None and k >= jump_at else 0.0)
        ma = (BASE["MEAN_ANOMALY"] + 360.0 * (BASE["MEAN_MOTION"] * dt + 0.5 * c * dt**2) + rng.normal(0, 0.003)) % 360
        rec = dict(BASE)
        rec.update(
            EPOCH=(t0 + timedelta(days=float(dt))).strftime("%Y-%m-%dT%H:%M:%S.%f"),
            MEAN_MOTION=round(n_rev, 8),
            MEAN_ANOMALY=round(ma, 4),
            RA_OF_ASC_NODE=round((BASE["RA_OF_ASC_NODE"] - 5.0 * dt + rng.normal(0, 0.003)) % 360, 4),
            ARG_OF_PERICENTER=round((BASE["ARG_OF_PERICENTER"] + 3.8 * dt) % 360, 4),
            ELEMENT_SET_NO=900 + k,
        )
        snaps.append(rec)
    doc = {"schema_version": 1, "_note": "SYNTHETIC test fixture", "norad_id": 25544, "snapshots": snaps}
    if source is not None:
        doc["data_source"] = source
    return doc


@pytest.fixture
def hist_file(tmp_path):
    def make(**kw):
        p = tmp_path / "hist.json"
        p.write_text(json.dumps(synthetic_history(**kw)), encoding="utf-8")
        return p

    return make


# ------------------------------------------------------------------ RIC frame


def test_ric_axes_for_a_circular_equatorial_orbit():
    rot = teme_to_ric_matrix(np.array([7000.0, 0, 0]), np.array([0, 7.5, 0]))
    np.testing.assert_allclose(rot, np.eye(3), atol=1e-12)  # R = x, I = y (along velocity), C = z (orbit normal)


def test_ric_roundtrip_and_orthonormality():
    rng = np.random.default_rng(0)
    r = rng.normal(size=(50, 3)) * 7000
    v = rng.normal(size=(50, 3)) * 7
    d = rng.normal(size=(50, 3))
    rot = teme_to_ric_matrix(r, v)
    np.testing.assert_allclose(np.einsum("nij,nkj->nik", rot, rot), np.broadcast_to(np.eye(3), (50, 3, 3)), atol=1e-12)
    np.testing.assert_allclose(ric_to_teme(teme_to_ric(d, r, v), r, v), d, atol=1e-10)
    np.testing.assert_allclose(np.linalg.norm(teme_to_ric(d, r, v), axis=1), np.linalg.norm(d, axis=1), atol=1e-10)


def test_arg_of_latitude_quadrants():
    r, v = np.array([7000.0, 0, 0]), np.array([0, 5.0, 5.0])  # inclined; at the ascending node
    assert arg_of_latitude(r, v) == pytest.approx(0.0, abs=1e-9)
    assert arg_of_latitude(np.array([0, 5000.0, 5000.0]), np.array([-7.0, 0, 0])) == pytest.approx(np.pi / 2, abs=1e-6)


# ------------------------------------------------------------------ data must be real


def test_loader_refuses_non_real_histories(hist_file):
    with pytest.raises(HistoryError, match="'SYNTHETIC', not REAL"):
        load_history(hist_file())
    unmarked = hist_file(source=None)  # no data_source field at all
    doc = json.loads(unmarked.read_text(encoding="utf-8"))
    doc["_note"] = "a history of element sets"  # and nothing in the note says what it is
    unmarked.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(HistoryError, match="'UNKNOWN', not REAL"):
        load_history(unmarked)  # unmarked files are not trusted either
    snaps, meta = load_history(hist_file(), allow_synthetic=True)
    assert len(snaps) == 40 and meta["data_source"] == "SYNTHETIC"


def test_missing_history_points_to_the_fetch_script(tmp_path):
    with pytest.raises(HistoryError, match="fetch_spacetrack_history"):
        load_history(tmp_path / "nope.json")


def test_too_short_history_is_refused(hist_file):
    with pytest.raises(HistoryError, match="at least 6"):
        load_history(hist_file(n=5), allow_synthetic=True)
    snaps, _ = load_history(hist_file(n=10), allow_synthetic=True)
    with pytest.raises(HistoryError, match="too short"):
        evaluate(snaps)


# ------------------------------------------------------------------ pairs, jumps, split


def test_pairs_ages_and_shapes(hist_file):
    snaps, _ = load_history(hist_file(), allow_synthetic=True)
    p = build_pairs(snaps)
    assert len(p) > 100 and p.res_ric.shape == (len(p), 3)
    assert np.all((p.age_d >= 1 / 24 - 1e-9) & (p.age_d <= 7.0))
    assert np.all(p.truth_idx > p.base_idx)
    np.testing.assert_allclose(p.age_d, (p.t_truth - p.t_base) / 86400.0)


def test_injected_burn_is_detected_and_pairs_spanning_it_are_excluded(hist_file):
    snaps, _ = load_history(hist_file(jump_at=20), allow_synthetic=True)
    jumps = detect_jumps(snaps)
    assert jumps == [snaps[20].t_unix]
    p = build_pairs(snaps)
    assert not np.any((p.t_base < jumps[0]) & (p.t_truth >= jumps[0]))  # nothing straddles the burn
    assert len(build_pairs(snaps, exclude_jumps=False)) > len(p)
    assert detect_jumps(load_history(hist_file(), allow_synthetic=True)[0]) == []  # smooth decay: no false alarms


def test_split_is_strictly_temporal_with_no_shared_snapshot(hist_file):
    snaps, _ = load_history(hist_file(), allow_synthetic=True)
    train, test, split_t = split_pairs(build_pairs(snaps), snaps)
    assert np.all(train.t_truth < split_t) and np.all(test.t_base > split_t)
    assert set(train.base_idx) | set(train.truth_idx) == set(train.base_idx) | set(train.truth_idx)
    assert not ({*train.base_idx, *train.truth_idx} & {*test.base_idx, *test.truth_idx})


# ------------------------------------------------------------------ regression plumbing (known synthetic truth)


def test_model_recovers_a_residual_known_by_construction():
    rng = np.random.default_rng(2)
    n = 400
    age = rng.uniform(0.05, 7, n)
    phase = rng.uniform(0, 2 * np.pi, n)
    res = np.column_stack([0.8 * np.cos(phase), 0.5 * age**2 + 2.0 * age, 0.3 * np.sin(2 * phase)]) + rng.normal(
        0, 0.05, (n, 3)
    )
    z = np.zeros(n, int)
    pairs = Pairs(z, z, z.astype(float), z.astype(float), age, phase, res)
    tr, te = pairs.take(np.arange(n) < 300), pairs.take(np.arange(n) >= 300)
    pred = ResidualModel("full").fit(tr).predict(te)
    assert np.sqrt(np.mean((te.res_ric - pred) ** 2)) < 0.2  # recovers the injected structure
    drift = ResidualModel("drift_only").fit(tr).predict(te)
    assert np.all(drift[:, [0, 2]] == 0)  # the simple model only touches the in-track axis
    with pytest.raises(ValueError):
        ResidualModel("nonsense")


def test_evaluate_plumbing_on_synthetic_fixture(hist_file):
    """On a fixture whose drift is smooth by construction the correction must help; this tests code, not SGP4."""
    snaps, _ = load_history(hist_file(), allow_synthetic=True)
    res = evaluate(snaps)
    full, drift = res["models"]["full"], res["models"]["drift_only"]
    assert res["pairs_train"] >= 30 and res["pairs_test"] >= 30
    assert full["rms_corrected_km"] < res["uncorrected"]["rms_km"]
    assert full["rms_reduction_pct_90ci"][0] > 0 and full["verdict"].startswith("improvement")
    assert drift["rms_reduction_pct"] > 0
    bins = full["by_age"]
    assert bins[0]["rms_uncorrected_km"] < bins[-1]["rms_uncorrected_km"]  # error grows with element-set age
    assert "ground truth" in " ".join(res["caveats"])


def test_no_improvement_is_reported_as_such(hist_file):
    """If the model cannot help, the verdict says so (an impossible-to-learn residual: pure noise in all axes)."""
    snaps, _ = load_history(hist_file(), allow_synthetic=True)
    pairs = build_pairs(snaps)
    rng = np.random.default_rng(5)
    noisy = Pairs(
        pairs.base_idx,
        pairs.truth_idx,
        pairs.t_base,
        pairs.t_truth,
        pairs.age_d,
        pairs.phase,
        rng.normal(0, 3.0, pairs.res_ric.shape),
    )
    train, test, _ = split_pairs(noisy, snaps)
    pred = ResidualModel("full").fit(train).predict(test)
    assert np.sqrt(np.mean((test.res_ric - pred) ** 2)) >= 0.95 * np.sqrt(np.mean(test.res_ric**2))  # nothing to learn


# ------------------------------------------------------------------ CLI


def test_cli_refuses_synthetic_by_default_and_never_touches_repo_results(hist_file, tmp_path, capsys):
    out, plot = tmp_path / "out.json", tmp_path / "plot.png"
    assert main(["--history", str(hist_file()), "--out", str(out), "--plot", str(plot)]) == 2
    assert "not REAL" in capsys.readouterr().err and not out.exists()
    assert main(["--history", str(hist_file()), "--out", str(out), "--plot", str(plot), "--allow-synthetic"]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["data_source"] == "SYNTHETIC" and "NOT accuracy results" in doc["_note"]
    assert plot.exists() and not str(doc["plot"]).startswith(("C:", "D:", "/"))  # no machine-specific absolute path


def test_cli_missing_file_exit_code(tmp_path, capsys):
    assert main(["--history", str(tmp_path / "missing.json"), "--out", str(tmp_path / "o.json")]) == 2
    assert "fetch_spacetrack_history" in capsys.readouterr().err
