"""WP-08: conjunction screening of an ascent path against the catalog -> risk.json.

Pipeline (all vectorised with sgp4's SatrecArray, chunked to bound memory):
  1. prefilter  - drop objects whose altitude shell [perigee, apogee] or latitude reach cannot come within
                  threshold + margin of the path (cheap, from catalog columns only);
  2. coarse     - propagate the survivors on a coarse grid (default 10 s), distance to the path in ECEF;
  3. fine       - objects whose coarse minimum is below threshold + slack (slack = max relative speed x dt/2),
                  plus the K (100) closest by coarse minimum, are re-propagated at the path's own 1 Hz grid;
  4. refine     - the closest few are re-propagated at 0.1 s around the minimum (path linearly interpolated).

Frames: SGP4 gives TEME (km); it is rotated to ECEF with core.frames (GMST) and compared with the
trajectory's geodetic points converted to ECEF. Distances are straight-line, in km.

Accuracy caveats (also written into risk.json metadata): TLE positions carry kilometre-level error that grows
with the age of the element set; thresholds and the score are illustrative, not agency keep-out volumes;
objects with catalog numbers >= 100000 are not in the TLE catalog and are NOT screened; objects SGP4 cannot
propagate at a time are skipped for that time (counted); the rocket is a point with no stage debris.

Run:  python -m catalog.screen --windows data/results/windows_cargo_iss_cape_canaveral.json \
                               --trajectory data/results/trajectory_cargo_iss_w0.json [--offline]
      python -m catalog.screen --benchmark

Owner: Yash (lane: catalog/).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sgp4.api import WGS72, Satrec, SatrecArray

from catalog.fetch import OUT_PATH as CATALOG_PATH
from catalog.fetch import build_catalog, load_catalog, sources_from_sample
from catalog.omm import satrec_from_row
from contracts.validate import validate
from core.constants import MU_M3_S2, R_E_M
from core.frames import ecef_to_teme, geodetic_to_ecef, teme_to_ecef

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "data" / "results"
R_E_KM = R_E_M / 1000.0
MAX_REL_SPEED_KMS = 18.0  # head-on LEO (7.8) + ascending vehicle (<= ~8) with margin
CHUNK = 1500  # objects per SGP4 batch: (chunk x T x 3) float64 stays well under ~100 MB for T <= 2000

TLE_ACCURACY_NOTE = (
    "TLE-based positions carry kilometre-level error that grows with element-set age (typically a few km per "
    "day along track). Thresholds and the risk score are illustrative; real launch collision avoidance uses larger, "
    "agency-specific keep-out volumes and covariance data. Objects with catalog numbers >= 100000 are not in the "
    "TLE catalog and are not screened."
)


@dataclass
class Track:
    t_unix: np.ndarray  # (T,) UTC seconds, ascending
    r_km: np.ndarray  # (T,3) ECEF km


@dataclass
class Hit:
    norad_id: int
    name: str
    min_miss_km: float
    t_closest_unix: float


@dataclass
class ScreenResult:
    hits: list[Hit]  # refined, ascending by min_miss_km
    stats: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers


def path_from_trajectory(traj: dict, t_launch_unix: float | None = None) -> Track:
    """Trajectory contract dict -> ECEF path. ``t_launch_unix`` time-shifts the ascent to another window."""
    t0 = traj["t0_unix"] if t_launch_unix is None else t_launch_unix
    t = t0 + np.asarray(traj["t_s"], dtype=float)
    r = geodetic_to_ecef(traj["lat_deg"], traj["lon_deg"], traj["alt_m"]) / 1000.0
    return Track(t, r)


def _jd_fr(t_unix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    day = np.floor(t_unix / 86400.0)
    return 2440587.5 + day, (t_unix - day * 86400.0) / 86400.0


def _interp_path(path: Track, t: np.ndarray) -> np.ndarray:
    return np.stack([np.interp(t, path.t_unix, path.r_km[:, k]) for k in range(3)], axis=-1)


def object_shell(df):
    """(perigee_km, apogee_km, max_latitude_deg) per object from catalog columns (Kozai mean motion, so approximate)."""
    n = df["mean_motion_rev_day"].to_numpy() * 2.0 * np.pi / 86400.0
    a_km = (MU_M3_S2 / n**2) ** (1.0 / 3.0) / 1000.0
    e = df["ecc"].to_numpy()
    incl = df["incl_deg"].to_numpy()
    return a_km * (1.0 - e) - R_E_KM, a_km * (1.0 + e) - R_E_KM, np.minimum(incl, 180.0 - incl)


def prefilter_mask(df, path: Track, threshold_km: float, margin_km: float = 60.0):
    """Boolean mask of objects that could come within threshold of the path (altitude shell + latitude reach)."""
    lat_lo_hi = _path_alt_lat(path)
    alt_lo, alt_hi, lat_abs_lo, _lat_abs_hi = lat_lo_hi
    perigee, apogee, max_lat = object_shell(df)
    reach = threshold_km + margin_km
    alt_ok = (perigee <= alt_hi + reach) & (apogee >= alt_lo - reach)
    deg = np.degrees(reach / R_E_KM)
    lat_ok = max_lat >= lat_abs_lo - deg
    return alt_ok & lat_ok


def _path_alt_lat(path: Track):
    r = np.linalg.norm(path.r_km, axis=1)
    alt = r - R_E_KM  # geocentric altitude proxy (ellipsoid flattening < 22 km; covered by the margin)
    lat = np.degrees(np.arcsin(path.r_km[:, 2] / r))
    lo_abs = 0.0 if lat.min() <= 0.0 <= lat.max() else float(np.abs(lat).min())
    return float(alt.min()), float(alt.max()), lo_abs, float(np.abs(lat).max())


def propagate_ecef(sats: list[Satrec], t_unix: np.ndarray) -> np.ndarray:
    """ECEF km positions, shape (N, T, 3); NaN where SGP4 reports an error (decayed, diverged, ...)."""
    jd, fr = _jd_fr(t_unix)
    err, r, _v = SatrecArray(sats).sgp4(jd, fr)
    r_ecef = teme_to_ecef(r, t_unix)
    r_ecef[err != 0] = np.nan
    return r_ecef


def _min_dist(sats, t_unix, path_r_km):
    """For each object: (min distance km over the grid, index of the minimum, n NaN samples)."""
    out_d, out_i, out_bad = [], [], []
    for s in range(0, len(sats), CHUNK):
        pos = propagate_ecef(sats[s : s + CHUNK], t_unix)
        d = np.linalg.norm(pos - path_r_km[None, :, :], axis=-1)
        bad = np.isnan(d)
        d[bad] = np.inf
        out_d.append(d.min(axis=1))
        out_i.append(d.argmin(axis=1))
        out_bad.append(bad.sum(axis=1))
    return np.concatenate(out_d), np.concatenate(out_i), np.concatenate(out_bad)


# --------------------------------------------------------------------------- parking-orbit coast


def _classical(r, v):
    """Osculating (n rad/min, e, i, raan, argp, M) from TEME r (km), v (km/s)."""
    mu = MU_M3_S2 / 1e9
    rn, vn = np.linalg.norm(r), np.linalg.norm(v)
    h = np.cross(r, v)
    node = np.cross([0.0, 0.0, 1.0], h)
    evec = ((vn**2 - mu / rn) * r - np.dot(r, v) * v) / mu
    e = np.linalg.norm(evec)
    a = 1.0 / (2.0 / rn - vn**2 / mu)
    i = math.acos(h[2] / np.linalg.norm(h))
    raan = math.atan2(node[1], node[0]) % (2 * math.pi)
    # circular-orbit safe: measure argument of latitude from the node, split between argp and M
    u = math.atan2(np.dot(np.cross(node, r), h) / np.linalg.norm(h), np.dot(node, r)) % (2 * math.pi)
    if e < 1e-6:
        argp, nu = 0.0, u
    else:
        argp = math.atan2(np.dot(np.cross(node, evec), h) / np.linalg.norm(h), np.dot(node, evec)) % (2 * math.pi)
        nu = (u - argp) % (2 * math.pi)
    ecc_anom = 2.0 * math.atan2(math.sqrt(1 - e) * math.sin(nu / 2), math.sqrt(1 + e) * math.cos(nu / 2))
    m = (ecc_anom - e * math.sin(ecc_anom)) % (2 * math.pi)
    n = math.sqrt(mu / a**3) * 60.0
    return np.array([n, e, i, raan, argp, m])


def _make_sat(x, t_unix: float) -> Satrec:
    sat = Satrec()
    n, e, i, raan, argp, m = x
    epoch_days = t_unix / 86400.0 + 2440587.5 - 2433281.5
    sat.sgp4init(WGS72, "i", 99999, epoch_days, 0.0, 0.0, 0.0, max(e, 1e-9), argp, i, m, n, raan)
    return sat


def _state(sat: Satrec, t_unix: float):
    jd, fr = _jd_fr(np.array([t_unix]))
    err, r, v = sat.sgp4_array(jd, fr)
    if err[0] != 0:
        raise ValueError(f"SGP4 error {int(err[0])} while fitting the parking orbit")
    return r[0], v[0]


def fit_vehicle_sat(r_teme_km, v_teme_kms, t_unix: float, tol_km: float = 1e-3) -> Satrec:
    """SGP4 element set whose state at ``t_unix`` reproduces an osculating state.

    Treating osculating elements as SGP4 mean elements would put the semi-major axis off by ~10 km (J2 short-period
    terms), i.e. ~100 km of along-track error after two hours. A Newton differential correction on the six elements
    (numeric Jacobian through SGP4 itself) removes that.
    """
    target = np.concatenate([r_teme_km, v_teme_kms])
    x = _classical(np.asarray(r_teme_km), np.asarray(v_teme_kms))
    scale = np.array([1e-6, 1e-6, 1e-6, 1e-6, 1e-6, 1e-6])
    for _ in range(12):
        r0, v0 = _state(_make_sat(x, t_unix), t_unix)
        res = target - np.concatenate([r0, v0])
        if np.linalg.norm(res[:3]) < tol_km and np.linalg.norm(res[3:]) < tol_km * 1e-3:
            return _make_sat(x, t_unix)
        jac = np.empty((6, 6))
        for k in range(6):
            dx = np.zeros(6)
            dx[k] = scale[k]
            r1, v1 = _state(_make_sat(x + dx, t_unix), t_unix)
            jac[:, k] = (np.concatenate([r1, v1]) - np.concatenate([r0, v0])) / scale[k]
        step, *_ = np.linalg.lstsq(jac, res, rcond=1e-10)
        x = x + step
        x[1] = abs(x[1])
    raise ValueError(f"parking-orbit fit did not converge (residual {np.linalg.norm(res[:3]):.3f} km)")


def vehicle_state_teme(traj_track: Track, k: int):
    """TEME (km, km/s) state at sample k from central-difference ECEF velocity."""
    t = traj_track.t_unix
    r_m = traj_track.r_km[k] * 1000.0
    v_ecef = (traj_track.r_km[k + 1] - traj_track.r_km[k - 1]) * 1000.0 / (t[k + 1] - t[k - 1])
    r_teme, v_teme = ecef_to_teme(r_m, t[k], v_ecef)
    return r_teme / 1000.0, v_teme / 1000.0


def orbit_summary(r_teme_km, v_teme_kms) -> tuple[float, float, float]:
    """(perigee_km, apogee_km, incl_deg) altitudes above the equatorial radius from an osculating state."""
    n, e, i, *_ = _classical(np.asarray(r_teme_km), np.asarray(v_teme_kms))
    a = (MU_M3_S2 / 1e9 / (n / 60.0) ** 2) ** (1.0 / 3.0)
    return a * (1.0 - e) - R_E_KM, a * (1.0 + e) - R_E_KM, math.degrees(i)


def with_coast(
    track: Track, coast_hours: float, step_s: float = 1.0, final_orbit: dict | None = None, tol_km: float = 50.0
) -> tuple[Track, dict]:
    """Append a parking-orbit coast (SGP4 from the fitted insertion state) to an ascent track.

    If ``final_orbit`` (the trajectory contract's declared perigee/apogee) is given, the orbit implied by the track's
    own last positions must agree with it to ``tol_km``; otherwise this raises instead of coasting on a state that
    contradicts the producer's own claim (typically a frame problem: positions must be Earth-fixed WGS-84 geodetic).
    """
    if coast_hours <= 0:
        return track, {}
    k = len(track.t_unix) - 2  # last sample with a central difference
    r, v = vehicle_state_teme(track, k)
    perigee, apogee, incl = orbit_summary(r, v)
    if final_orbit is not None:
        err = max(abs(perigee - final_orbit["perigee_km"]), abs(apogee - final_orbit["apogee_km"]))
        if err > tol_km:
            raise ValueError(
                f"trajectory positions imply a {perigee:.0f} x {apogee:.0f} km orbit at insertion but final_orbit says "
                f"{final_orbit['perigee_km']:.0f} x {final_orbit['apogee_km']:.0f} km "
                f"(difference {err:.0f} km > {tol_km:.0f}); "
                "refusing to screen a coast from an inconsistent state. Check that lat/lon/alt are Earth-fixed WGS-84 "
                "geodetic (not an inertial or spherical-Earth ground track)."
            )
    sat = fit_vehicle_sat(r, v, float(track.t_unix[k]))
    t_end = track.t_unix[-1]
    t = t_end + np.arange(step_s, coast_hours * 3600.0 + step_s / 2, step_s)
    pos = propagate_ecef([sat], t)[0]
    info = {
        "coast_hours": coast_hours,
        "implied_orbit_at_insertion_km": [round(perigee, 1), round(apogee, 1)],
        "insertion_speed_inertial_kms": round(float(np.linalg.norm(v)), 3),
        "insertion_altitude_km": round(float(np.linalg.norm(track.r_km[-1]) - R_E_KM), 1),
    }
    return Track(np.concatenate([track.t_unix, t]), np.vstack([track.r_km, pos])), info


# --------------------------------------------------------------------------- screening


class Screener:
    """Screens paths against a catalog DataFrame (contract columns). Satrecs are built once per object."""

    def __init__(self, df, threshold_km: float = 25.0, exclude_norad: tuple[int, ...] = ()):
        self.df = df[~df["norad_id"].isin(exclude_norad)].reset_index(drop=True)
        self.threshold_km = threshold_km
        self._records = self.df.to_dict("records")
        self._sat: dict[int, Satrec] = {}

    def _sats(self, idx) -> list[Satrec]:
        """Satellites for df positions ``idx``, built from the OMM columns (works for catalog numbers >= 100000)."""
        out = []
        for i in idx:
            row = self._records[i]
            nid = int(row["norad_id"])
            if nid not in self._sat:
                self._sat[nid] = satrec_from_row(row)
            out.append(self._sat[nid])
        return out

    def screen(
        self,
        path: Track,
        coarse_dt_s: float = 10.0,
        report_km: float | None = None,
        top_k: int = 100,
        refine_n: int = 5,
        use_prefilter: bool = True,
    ) -> ScreenResult:
        t_start = time.perf_counter()
        thr = self.threshold_km
        report = report_km if report_km is not None else 2.0 * thr
        mask = prefilter_mask(self.df, path, thr) if use_prefilter else np.ones(len(self.df), dtype=bool)
        idx = np.flatnonzero(mask)
        if idx.size < top_k:  # always have a closest object to report: top up with the nearest altitude shells
            perigee, apogee, _ = object_shell(self.df)
            alt_lo, alt_hi, *_ = _path_alt_lat(path)
            gap = np.maximum.reduce([perigee - alt_hi, alt_lo - apogee, np.zeros(len(self.df))])
            idx = np.union1d(idx, np.argsort(gap, kind="stable")[:top_k])
        stats = {"catalog_objects": len(self.df), "after_prefilter": int(mask.sum()), "screened": int(idx.size)}
        if idx.size == 0:
            stats["runtime_s"] = time.perf_counter() - t_start
            return ScreenResult([], stats)
        sats = self._sats(idx)

        # --- coarse
        step = max(1, int(round(coarse_dt_s / max(np.median(np.diff(path.t_unix)), 1e-9))))
        ci = np.unique(np.append(np.arange(0, len(path.t_unix), step), len(path.t_unix) - 1))
        d_c, _, bad_c = _min_dist(sats, path.t_unix[ci], path.r_km[ci])
        slack = MAX_REL_SPEED_KMS * (coarse_dt_s / 2.0)
        order = np.argsort(d_c)
        cand = set(np.flatnonzero(d_c < report + slack).tolist()) | set(order[:top_k].tolist())
        cand = sorted(c for c in cand if np.isfinite(d_c[c]))
        stats.update(coarse_samples=int(ci.size), candidates=len(cand), sgp4_failures_coarse=int((bad_c > 0).sum()))

        # --- fine (path's own grid)
        csats = [sats[c] for c in cand]
        d_f, i_f, _ = _min_dist(csats, path.t_unix, path.r_km)
        # --- refine around the minimum, 0.1 s, linear path interpolation
        hits = []
        for rank in np.argsort(d_f):
            c = cand[rank]
            row = self.df.iloc[idx[c]]
            d_min, t_min = float(d_f[rank]), float(path.t_unix[i_f[rank]])
            if len(hits) < refine_n and np.isfinite(d_min):
                tt = np.arange(t_min - 1.5, t_min + 1.5001, 0.1)
                tt = tt[(tt >= path.t_unix[0]) & (tt <= path.t_unix[-1])]
                pos = propagate_ecef([csats[rank]], tt)[0]
                dd = np.linalg.norm(pos - _interp_path(path, tt), axis=-1)
                if np.isfinite(dd).any():
                    k = int(np.nanargmin(dd))
                    d_min, t_min = float(dd[k]), float(tt[k])
            if np.isfinite(d_min):
                hits.append(Hit(int(row["norad_id"]), str(row["name"]), d_min, t_min))
        hits.sort(key=lambda h: h.min_miss_km)
        stats["runtime_s"] = time.perf_counter() - t_start
        return ScreenResult(hits, stats)


# --------------------------------------------------------------------------- risk scoring


def classify(min_miss_km: float, threshold_km: float) -> tuple[float, str]:
    """(risk_score in [0,1], status). Illustrative: no-go < threshold/4, review < threshold, else go.

    score = exp(-3 (d/threshold)^2): 1.0 at 0 km, 0.47 at threshold/2, 0.05 at the threshold.
    """
    score = math.exp(-3.0 * (min_miss_km / threshold_km) ** 2)
    status = "no-go" if min_miss_km < threshold_km / 4.0 else "review" if min_miss_km < threshold_km else "go"
    return round(score, 4), status


def _utc(t: float) -> str:
    return datetime.fromtimestamp(int(round(t)), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_risk(
    windows: dict, traj: dict, df, threshold_km: float = 25.0, catalog_mode: str = "unknown", coast_hours: float = 0.0
) -> dict:
    """risk.json for every flyable window: the ascent in ``traj`` is time-shifted to each window's launch time."""
    screener = Screener(df, threshold_km, exclude_norad=(int(windows["target"]["norad_id"]),))
    results, per_window = [], []
    ages, coast_info = [], {}
    for w in windows["windows"]:
        if not w["flyable"]:
            continue
        path, coast_info = with_coast(
            path_from_trajectory(traj, w["t_launch_unix"]), coast_hours, final_orbit=traj["final_orbit"]
        )
        res = screener.screen(path)
        per_window.append({"window_id": w["id"], **{k: res.stats[k] for k in ("after_prefilter", "candidates")}})
        if not res.hits:
            continue
        h = res.hits[0]
        score, status = classify(h.min_miss_km, threshold_km)
        results.append(
            {
                "window_id": w["id"],
                "trajectory_id": traj["id"],
                "min_miss_km": round(h.min_miss_km, 2),
                "object": {"norad_id": h.norad_id, "name": h.name},
                "t_closest_utc": _utc(h.t_closest_unix),
                "risk_score": score,
                "status": status,
            }
        )
        row = screener.df[screener.df["norad_id"] == h.norad_id].iloc[0]
        epoch = datetime.fromisoformat(row["epoch_utc"].replace("Z", "+00:00")).timestamp()
        ages.append((h.t_closest_unix - epoch) / 86400.0)
    epochs = df["epoch_utc"]
    obj = {
        "schema_version": 1,
        "_note": (
            "REAL screening of a simulated (fictional-archetype, reduced-fidelity) ascent against real TLEs. "
            "Illustrative thresholds; not an operational collision-avoidance product."
        ),
        "threshold_km": threshold_km,
        "metadata": {
            "tle_accuracy_note": TLE_ACCURACY_NOTE,
            "method": "SGP4 (sgp4.SatrecArray) -> TEME->ECEF (GMST) vs trajectory geodetic->ECEF; "
            "prefilter by altitude "
            "shell and latitude reach; coarse 10 s pass, 1 Hz pass on candidates, 0.1 s refinement of the closest",
            "status_rule": "no-go if min_miss < threshold/4; review if < threshold; else go. "
            "risk_score = exp(-3 (d/threshold)^2)",
            "min_miss_km_note": "closest object found per window; values far above threshold are indicative only "
            "(guaranteed accurate for objects within 2x threshold; beyond that only the 100 closest coarse "
            "candidates are refined, and objects more than ~threshold+60 km away in altitude are prefiltered out)",
            "trajectory_reuse": f"trajectory {traj['id']} (rocket {traj['rocket_id']}) is time-shifted to each flyable "
            "window; descending (non-flyable) windows are not screened",
            "target_excluded_norad": int(windows["target"]["norad_id"]),
            "screened_span": (
                f"ascent {traj['t_s'][-1]:g} s + {coast_hours:g} h parking-orbit coast "
                "(SGP4 from the fitted insertion state)"
                if coast_hours > 0
                else f"ascent only ({traj['t_s'][-1]:g} s)"
            ),
            "coast": coast_info,
            "catalog_mode": catalog_mode,
            "catalog_objects": int(len(df)),
            "latest_tle_epoch_utc": str(epochs.max()),
            "closest_object_tle_age_days_at_closest_approach": [round(a, 1) for a in ages],
            "per_window": per_window,
        },
        "results": results,
    }
    return validate("risk", obj)


# --------------------------------------------------------------------------- benchmark


def benchmark(n_objects: int = 30_000, hours: float = 2.0, step_s: float = 10.0, chunk: int = CHUNK) -> dict:
    """Time the SGP4 core on n_objects x (hours / step_s) samples, using the real catalog replicated to size.

    This is the HANDOFF budget check (~30k objects x 2 h at <= 10 s in roughly 60 s); it is the *unfiltered* worst case.
    """
    try:
        df = load_catalog(CATALOG_PATH)
    except FileNotFoundError:
        df = build_catalog(sources_from_sample()[0])[0]
    reps = int(math.ceil(n_objects / len(df)))
    records = df.to_dict("records") * reps
    t0 = time.perf_counter()
    sats = [satrec_from_row(r) for r in records[:n_objects]]
    t_init = time.perf_counter() - t0
    t = df["epoch_utc"].map(lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()).max() + np.arange(
        0.0, hours * 3600.0, step_s
    )
    path = np.zeros((len(t), 3)) + np.array([R_E_KM + 400.0, 0.0, 0.0])
    t0 = time.perf_counter()
    n_ok = 0
    for s in range(0, len(sats), chunk):
        pos = propagate_ecef(sats[s : s + chunk], t)
        d = np.linalg.norm(pos - path[None], axis=-1)
        n_ok += int(np.isfinite(d).sum())
    t_prop = time.perf_counter() - t0
    return {
        "objects": len(sats),
        "steps": len(t),
        "samples": len(sats) * len(t),
        "satrec_init_s": round(t_init, 2),
        "propagate_and_distance_s": round(t_prop, 2),
        "samples_per_s": int(len(sats) * len(t) / t_prop),
        "finite_fraction": round(n_ok / (len(sats) * len(t)), 4),
    }


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Screen an ascent trajectory against the catalog -> risk json")
    ap.add_argument("--windows", type=Path, help="windows_*.json (flyable windows are screened)")
    ap.add_argument("--trajectory", type=Path, help="trajectory json (time-shifted to every flyable window)")
    ap.add_argument("--threshold-km", type=float, default=25.0)
    ap.add_argument("--offline", action="store_true", help="use the committed offline catalog sample")
    ap.add_argument("--coast-hours", type=float, default=0.0, help="also screen this many hours of parking-orbit coast")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--benchmark", action="store_true", help="time the SGP4 core: 30k objects x 2 h at 10 s")
    args = ap.parse_args(argv)
    t_cli = time.perf_counter()
    if args.benchmark:
        print(json.dumps(benchmark(), indent=2))
        return 0
    if not (args.windows and args.trajectory):
        ap.error("--windows and --trajectory are required (or use --benchmark)")
    try:
        windows = json.loads(args.windows.read_text(encoding="utf-8"))
        traj = json.loads(args.trajectory.read_text(encoding="utf-8"))
        validate("windows", windows)
        validate("trajectory", traj)
        if args.offline or not CATALOG_PATH.exists():
            if not args.offline:
                print("WARNING: catalog.parquet missing; using the offline sample", file=sys.stderr)
            df, mode = build_catalog(sources_from_sample()[0])[0], "offline-sample"
        else:
            df, mode = load_catalog(CATALOG_PATH), "full-catalog"
        risk = build_risk(windows, traj, df, args.threshold_km, mode, args.coast_hours)
    except Exception as e:  # noqa: BLE001 - CLI boundary: report, do not traceback
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    out = (
        args.out
        or RESULTS_DIR / f"risk_{windows['profile']}_{args.windows.stem.split(windows['profile'] + '_')[-1]}.json"
    )
    out.write_text(json.dumps(risk, indent=2) + "\n", encoding="utf-8")
    for r in risk["results"]:
        print(
            f"{r['window_id']:4s} {r['status']:7s} {r['min_miss_km']:8.2f} km  "
            f"{r['object']['name']} ({r['object']['norad_id']})"
        )
    print(f"wrote {out}  ({time.perf_counter() - t_cli:.2f} s, catalog {mode}, {len(df)} objects)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
