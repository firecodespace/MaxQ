"""WP-08 acceptance: conjunction screening. Owner: Yash.

Independent of Shiva's trajectory files: a synthetic ascent path is built here. Uses the committed offline catalog
sample for the background population (no network).
"""

from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from catalog.fetch import build_catalog, get_tle, sources_from_sample
from catalog.omm import satrec_from_row
from catalog.propagate import propagate_teme, satrec_from_tle
from catalog.screen import (
    R_E_KM,
    Screener,
    Track,
    build_risk,
    classify,
    fit_vehicle_sat,
    object_shell,
    orbit_summary,
    path_from_trajectory,
    prefilter_mask,
    propagate_ecef,
    vehicle_state_teme,
    with_coast,
)
from contracts.validate import validate
from core.constants import MU_M3_S2
from core.frames import ecef_to_teme, teme_to_ecef
from mission.windows import compute_windows

T0 = datetime(2026, 11, 10, 0, 0, 0, tzinfo=UTC).timestamp()
MU_KM = MU_M3_S2 / 1e9


def synthetic_traj(t0=T0, n=451):
    s = np.arange(n, dtype=float)
    f = s / (n - 1)
    return {
        "schema_version": 1,
        "id": "traj_synth",
        "rocket_id": "synth",
        "window_id": "w0",
        "t0_unix": int(t0),
        "seed": 1,
        "t_s": s.tolist(),
        "lat_deg": (28.56 + 6.0 * f**2).tolist(),
        "lon_deg": (-80.58 + 9.0 * f**2).tolist(),
        "alt_m": (190_000.0 * f**2).tolist(),
        "speed_mps": [0.0] * n,
        "q_pa": [0.0] * n,
        "mass_kg": [1.0] * n,
        "events": [],
        "final_orbit": {"perigee_km": 190.0, "apogee_km": 200.0, "incl_deg": 40.0, "ecc": 0.001},
        "params": {},
    }


@pytest.fixture(scope="module")
def df():
    return build_catalog(sources_from_sample()[0])[0]


@pytest.fixture(scope="module")
def track():
    return path_from_trajectory(synthetic_traj())


def inject(df, sats):
    """Add synthetic objects (given as {norad: Satrec}) to the catalog DataFrame with matching element columns."""
    rows = []
    base = df.iloc[0].to_dict()
    for nid, sat in sats.items():
        rows.append(
            {
                **base,
                "norad_id": nid,
                "name": f"SYNTH-{nid}",
                "group": "debris",
                "mean_motion_rev_day": sat.no_kozai * 1440.0 / (2 * math.pi),
                "incl_deg": math.degrees(sat.inclo),
                "ecc": float(sat.ecco),
            }
        )
    return pd.concat([df, pd.DataFrame(rows)], ignore_index=True)


def screener_with(df, sats, threshold_km=25.0):
    """Screener over df + synthetic objects, with their exact (fitted) Satrecs preloaded instead of TLEs."""
    scr = Screener(inject(df, sats), threshold_km)
    scr._sat.update(sats)
    return scr


def conjunction_sat(track, k, miss_km, heading=1.0):
    """Satrec passing ``miss_km`` from the path at sample k, offset perpendicular to the relative velocity."""
    r, v = vehicle_state_teme(track, k)
    r_hat = r / np.linalg.norm(r)
    direction = np.cross(r_hat, v / np.linalg.norm(v)) * heading
    direction /= np.linalg.norm(direction)
    v_obj = math.sqrt(MU_KM / np.linalg.norm(r)) * direction
    rel = v_obj - v
    d = np.cross(rel, r_hat)
    d /= np.linalg.norm(d)
    return fit_vehicle_sat(r + miss_km * d, v_obj, float(track.t_unix[k]))


# ------------------------------------------------------------------ the synthetic object is flagged


@pytest.mark.parametrize(("miss", "status"), [(3.0, "no-go"), (15.0, "review"), (40.0, "go")])
def test_screen_flags_synthetic(df, track, miss, status):
    k = 350
    sat = conjunction_sat(track, k, miss)
    res = screener_with(df, {90001: sat}).screen(track)
    hit = res.hits[0]
    assert hit.norad_id == 90001 and hit.name == "SYNTH-90001"
    assert hit.min_miss_km == pytest.approx(miss, abs=0.5)
    assert abs(hit.t_closest_unix - track.t_unix[k]) <= 1.0
    assert classify(hit.min_miss_km, 25.0)[1] == status


def test_far_object_is_not_flagged(df, track):
    sat = conjunction_sat(track, 350, 400.0)
    res = screener_with(df, {90002: sat}).screen(track)
    assert all(h.min_miss_km > 100 for h in res.hits)


def test_engine_matches_dense_brute_force(df, track):
    """Closest hits from the 3-stage engine equal a brute-force 0.1 s scan over every object (no prefilter)."""
    sats = {90010: conjunction_sat(track, 380, 6.0), 90011: conjunction_sat(track, 430, 18.0, heading=-1.0)}
    scr = screener_with(df, sats)
    res = scr.screen(track, use_prefilter=False, refine_n=10)
    by_id = {h.norad_id: h.min_miss_km for h in res.hits}

    tt = np.arange(track.t_unix[0], track.t_unix[-1], 0.1)
    path_dense = np.stack([np.interp(tt, track.t_unix, track.r_km[:, c]) for c in range(3)], -1)
    for nid, want_close in ((90010, 6.0), (90011, 18.0)):
        pos = propagate_ecef([scr._sats([int(np.flatnonzero(scr.df.norad_id == nid)[0])])[0]], tt)[0]
        brute = np.nanmin(np.linalg.norm(pos - path_dense, axis=-1))
        assert brute == pytest.approx(want_close, abs=0.5)
        assert by_id[nid] == pytest.approx(brute, abs=0.25)


def test_close_approach_time_is_refined_to_subsecond(df, track):
    sat = conjunction_sat(track, 400, 4.0)
    hit = screener_with(df, {90020: sat}).screen(track).hits[0]
    assert abs(hit.t_closest_unix - track.t_unix[400]) <= 0.3


# ------------------------------------------------------------------ prefilter and classification


def test_prefilter_drops_high_orbits_and_keeps_low(df, track):
    mask = prefilter_mask(df, track, 25.0)
    perigee, apogee, _ = object_shell(df)
    assert not mask[(perigee > 800)].any()  # nothing above ~800 km perigee can reach a <=200 km path
    assert mask.sum() < 0.5 * len(df)
    low = df.iloc[0:1].copy()  # a synthetic low object is kept
    low["mean_motion_rev_day"], low["ecc"], low["incl_deg"] = 16.3, 0.001, 40.0
    assert prefilter_mask(low, track, 25.0)[0]


def test_classify_boundaries():
    assert classify(0.0, 25.0) == (1.0, "no-go")
    assert classify(6.24, 25.0)[1] == "no-go" and classify(6.26, 25.0)[1] == "review"
    assert classify(24.9, 25.0)[1] == "review" and classify(25.0, 25.0)[1] == "go"
    scores = [classify(d, 25.0)[0] for d in (0, 5, 10, 25, 50)]
    assert scores == sorted(scores, reverse=True) and scores[3] < 0.06


# ------------------------------------------------------------------ parking-orbit fit and coast


def test_fit_reproduces_state_and_propagation(df):
    _, l1, l2 = get_tle(df, 25544)
    iss = satrec_from_tle(l1, l2)
    t = (iss.jdsatepoch - 2440587.5) * 86400.0 + iss.jdsatepochF * 86400.0 + 600.0
    r, v = propagate_teme(iss, t)
    fit = fit_vehicle_sat(r[0], v[0], t)
    for dt in (0.0, 1800.0, 3600.0, 7200.0):
        a, b = propagate_teme(iss, t + dt)[0][0], propagate_teme(fit, t + dt)[0][0]
        assert np.linalg.norm(a - b) < 1.0, dt  # km, vs ~100 km if osculating were used as mean elements


def test_with_coast_matches_direct_sgp4_and_checks_final_orbit(df):
    _, l1, l2 = get_tle(df, 25544)
    iss = satrec_from_tle(l1, l2)
    t_end = (iss.jdsatepoch - 2440587.5) * 86400.0 + iss.jdsatepochF * 86400.0 + 3600.0
    t = np.arange(t_end - 400.0, t_end + 0.5, 1.0)
    r_teme, v_teme = propagate_teme(iss, t)
    track = Track(t, teme_to_ecef(r_teme, t))
    peri, apo, _ = orbit_summary(r_teme[-2], v_teme[-2])
    ok = {"perigee_km": peri, "apogee_km": apo}
    longer, info = with_coast(track, 1.0, final_orbit=ok)
    assert len(longer.t_unix) == len(t) + 3600 and info["coast_hours"] == 1.0
    direct = teme_to_ecef(propagate_teme(iss, longer.t_unix[-1])[0], longer.t_unix[-1])[0]
    assert np.linalg.norm(longer.r_km[-1] - direct) < 2.0
    with pytest.raises(ValueError, match="inconsistent state"):
        with_coast(track, 1.0, final_orbit={"perigee_km": peri + 500.0, "apogee_km": apo + 500.0})
    same, none = with_coast(track, 0.0)
    assert same is track and none == {}


# ------------------------------------------------------------------ risk.json


def test_build_risk_contract_flyable_only_and_caveats(df):
    windows = compute_windows("crew_iss", "cape_canaveral", df, T0, 3, now_unix=T0)
    risk = build_risk(windows, synthetic_traj(), df, 25.0, "offline-sample")
    validate("risk", risk)
    flyable = {w["id"] for w in windows["windows"] if w["flyable"]}
    assert flyable and {r["window_id"] for r in risk["results"]} <= flyable
    assert "kilometre-level" in risk["metadata"]["tle_accuracy_note"]
    assert "not in the" in risk["metadata"]["tle_accuracy_note"]  # 6-digit catalog-number gap is stated
    assert risk["metadata"]["target_excluded_norad"] == 25544
    assert risk["metadata"]["catalog_mode"] == "offline-sample"
    assert all(r["trajectory_id"] == "traj_synth" and r["object"]["norad_id"] != 25544 for r in risk["results"])
    assert "REAL screening" in risk["_note"]


def test_build_risk_time_shifts_trajectory_to_each_window(df):
    windows = compute_windows("crew_iss", "cape_canaveral", df, T0, 3, now_unix=T0)
    risk = build_risk(windows, synthetic_traj(t0=0), df, 25.0)
    by_id = {w["id"]: w["t_launch_unix"] for w in windows["windows"]}
    for r in risk["results"]:
        t = datetime.strptime(r["t_closest_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
        assert by_id[r["window_id"]] - 1 <= t <= by_id[r["window_id"]] + 452


# ------------------------------------------------------------------ performance budget


def test_sgp4_core_meets_budget_scaled(df):
    """HANDOFF: ~30k objects x 2 h at <= 10 s steps in ~60 s. Check 3k x 2 h here, at 1/10 of the budget x 3 safety."""
    sats = [satrec_from_row(r) for r in df.to_dict("records")]
    sats = (sats * (3000 // len(sats) + 1))[:3000]
    t = T0 + np.arange(0.0, 7200.0, 10.0)
    r_path = np.tile([R_E_KM + 400.0, 0.0, 0.0], (len(t), 1))
    t0 = time.perf_counter()
    for s in range(0, len(sats), 1500):
        d = np.linalg.norm(propagate_ecef(sats[s : s + 1500], t) - r_path[None], axis=-1)
        assert d.shape == (1500, len(t))
    assert time.perf_counter() - t0 < 60.0 / 10.0 * 3.0


def test_teme_ecef_consistency_between_path_and_objects(track):
    """A TEME point converted to ECEF at the same instant lands on the path point it came from."""
    k = 100
    r_teme = ecef_to_teme(track.r_km[k], track.t_unix[k])
    np.testing.assert_allclose(teme_to_ecef(r_teme, track.t_unix[k]), track.r_km[k], atol=1e-9)


# ---------------------------------------------------------- guard: committed trajectories are frame-consistent

REAL_TRAJECTORIES = sorted((Path(__file__).resolve().parents[2] / "data" / "results").glob("trajectory_*.json"))


@pytest.mark.parametrize("path", REAL_TRAJECTORIES, ids=lambda p: p.name)
def test_committed_trajectory_positions_match_declared_final_orbit(path):
    """Regression guard for the WP-05 frame bug: lat/lon/alt must be Earth-fixed WGS-84 geodetic, so the orbit
    implied by the last positions must agree with the trajectory's own final_orbit (what with_coast enforces)."""
    traj = json.loads(path.read_text(encoding="utf-8"))
    track = path_from_trajectory(traj)
    longer, info = with_coast(track, 0.1, final_orbit=traj["final_orbit"])  # raises ValueError if inconsistent
    assert info["implied_orbit_at_insertion_km"][0] > 100 and len(longer.t_unix) > len(track.t_unix)
    assert 7.6 < info["insertion_speed_inertial_kms"] < 7.95  # LEO circular speed, not circular + Earth rotation
