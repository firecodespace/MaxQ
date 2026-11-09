"""Generate Yash's dummy data files in data/dummy/ (windows, risk, clusters, catalog).

Deterministic: re-running produces identical files. Everything here is DUMMY data:
shapes follow HANDOFF.md section 5, numbers are illustrative, never real results.
Rocket / trajectory / manifest / result dummies are Shiva's (data/dummy/*).

    python scripts/make_dummy_data.py
"""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "dummy"
MU_KM3_S2 = 398600.4418  # km^3/s^2, same as core/constants.py MU (m^3/s^2) / 1e9
R_E_KM = 6378.137

NOTE = (
    "DUMMY DATA - shapes follow HANDOFF.md section 5; numbers are illustrative and are "
    "not real launch windows, screening results, clusters or orbital elements."
)


def utc(t_unix: int) -> str:
    return datetime.fromtimestamp(t_unix, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def dump(name: str, obj: dict) -> None:
    (OUT / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    print("wrote", name)


# ------------------------------------------------------------------ windows
T0 = 1794286000  # 2026-11-10T04:46:40Z (given)
assert utc(T0) == "2026-11-10T04:46:40Z"
DAY_SHIFT = 86400 - 1320  # ISS-plane crossings arrive ~22 min earlier each day (dummy value)


def windows() -> dict:
    spec = [  # id, t_unix, pass, azimuth, phase, fast_h, slow_h, flyable
        ("w0", T0, "ascending", 44.9, 31.2, 3.2, 25.0, True),
        ("w1", T0 + 43320, "descending", 135.1, 214.6, 4.1, 26.0, False),
        ("w2", T0 + DAY_SHIFT, "ascending", 44.9, 118.4, 6.0, 25.0, True),
        ("w3", T0 + 43320 + DAY_SHIFT, "descending", 135.1, 301.8, 4.6, 27.0, False),
        ("w4", T0 + 2 * DAY_SHIFT, "ascending", 44.9, 205.7, 9.4, 26.0, True),
    ]
    return {
        "schema_version": 1,
        "_note": NOTE,
        "profile": "crew_iss",
        "site": {"name": "Cape Canaveral", "lat_deg": 28.5, "lon_deg": -80.6},
        "target": {"norad_id": 25544, "tle_epoch_utc": "2026-11-10T00:00:00Z"},
        "generated_utc": "2026-11-10T01:00:00Z",
        "windows": [
            {
                "id": i,
                "t_launch_unix": t,
                "t_launch_utc": utc(t),
                "azimuth_deg": az,
                "plane_error_deg": 0.0,
                "pass": p,
                "phase_angle_deg": ph,
                "rendezvous_hours_fast": fast,
                "rendezvous_hours_slow": slow,
                "flyable": fly,
            }
            for i, t, p, az, ph, fast, slow, fly in spec
        ],
    }


# ------------------------------------------------------------------ risk
def risk() -> dict:
    return {
        "schema_version": 1,
        "_note": NOTE,
        "threshold_km": 25,
        "metadata": {
            "tle_accuracy_note": "DUMMY. Real screening uses TLEs with kilometer-level position error; "
            "thresholds here are illustrative, not agency keep-out volumes."
        },
        "results": [
            {
                "window_id": "w0",
                "trajectory_id": "traj_medlift_a_w0",
                "min_miss_km": 41.3,
                "object": {"norad_id": 99001, "name": "DUMMY DEB A"},
                "t_closest_utc": utc(T0 + 370),
                "risk_score": 0.12,
                "status": "go",
            },
            {
                "window_id": "w2",
                "trajectory_id": "traj_medlift_a_w2",
                "min_miss_km": 14.2,
                "object": {"norad_id": 99002, "name": "DUMMY DEB B"},
                "t_closest_utc": utc(T0 + DAY_SHIFT + 410),
                "risk_score": 0.55,
                "status": "review",
            },
            {
                "window_id": "w4",
                "trajectory_id": "traj_medlift_a_w4",
                "min_miss_km": 3.8,
                "object": {"norad_id": 99003, "name": "DUMMY DEB C"},
                "t_closest_utc": utc(T0 + 2 * DAY_SHIFT + 395),
                "risk_score": 0.93,
                "status": "no-go",
            },
        ],
    }


# ------------------------------------------------------------------ catalog (10 rows)
def _checksum(line: str) -> str:
    return str(sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line) % 10)


def make_tle(norad, intl, incl, raan, ecc, argp, m_anom, mm, epoch_yy, epoch_doy):
    l1 = f"1 {norad:05d}U {intl:<8s} {epoch_yy:02d}{epoch_doy:012.8f} {' .00000000'} {' 00000-0'} {' 00000-0'} 0 {1:4d}"
    l2 = f"2 {norad:05d} {incl:8.4f} {raan:8.4f} {round(ecc * 1e7):07d} {argp:8.4f} {m_anom:8.4f} {mm:11.8f}{1:5d}"
    assert len(l1) == 68 and len(l2) == 68, (len(l1), len(l2))
    return l1 + _checksum(l1), l2 + _checksum(l2)


def catalog() -> dict:
    # name, norad, group, incl, ecc, mean_motion, raan, argp, mean_anomaly
    spec = [
        ("DUMMY ISS (ZARYA)", 25544, "stations", 51.64, 0.0004, 15.50, 120.0, 90.0, 270.0),
        ("DUMMY STARLINK-1", 99101, "starlink", 53.05, 0.0001, 15.06, 100.0, 80.0, 10.0),
        ("DUMMY STARLINK-2", 99102, "starlink", 53.05, 0.0001, 15.06, 101.0, 80.0, 40.0),
        ("DUMMY STARLINK-3", 99103, "starlink", 53.05, 0.0001, 15.06, 102.0, 80.0, 70.0),
        ("DUMMY NAVSTAR-1", 99201, "gps-ops", 55.0, 0.005, 2.0056, 20.0, 45.0, 120.0),
        ("DUMMY NAVSTAR-2", 99202, "gps-ops", 55.0, 0.005, 2.0056, 80.0, 45.0, 240.0),
        ("DUMMY GEOSAT-1", 99301, "geo", 0.05, 0.0002, 1.0027, 75.0, 180.0, 30.0),
        ("DUMMY GEOSAT-2", 99302, "geo", 0.04, 0.0002, 1.0027, 76.0, 180.0, 200.0),
        ("DUMMY DEB A", 99001, "debris", 82.5, 0.02, 14.3, 200.0, 10.0, 300.0),
        ("DUMMY DEB B", 99002, "debris", 74.0, 0.01, 14.0, 250.0, 200.0, 60.0),
    ]
    rows = []
    for name, norad, group, incl, ecc, mm, raan, argp, ma in spec:
        l1, l2 = make_tle(norad, "00000A", incl, raan, ecc, argp, ma, mm, 26, 314.0)  # 2026-11-10T00:00:00Z
        a_km = (MU_KM3_S2 / (mm * 2 * math.pi / 86400) ** 2) ** (1 / 3)
        rows.append(
            {
                "norad_id": norad,
                "name": name,
                "group": group,
                "tle1": l1,
                "tle2": l2,
                "epoch_utc": "2026-11-10T00:00:00Z",
                "incl_deg": incl,
                "ecc": ecc,
                "mean_motion_rev_day": mm,
                "raan_deg": raan,
                "alt_km_approx": round(a_km - R_E_KM, 1),
            }
        )
    return {"schema_version": 1, "_note": NOTE + " TLEs are synthetic (well-formed, valid checksums).", "rows": rows}


# ------------------------------------------------------------------ clusters
def clusters(cat: dict) -> dict:
    rng = random.Random(7)
    centers = {0: (-6.0, 2.0), 1: (7.0, 5.0), 2: (2.0, -6.0), 3: (-4.0, -5.0)}
    cluster_of = {"stations": 0, "starlink": 0, "gps-ops": 2, "geo": 1, "debris": 3}
    defs = [
        (0, "LEO shell ~53 deg (Starlink-like)", 5400, 53.05, 15.06, 0.0001),
        (1, "GEO belt", 540, 0.05, 1.0027, 0.0002),
        (2, "MEO navigation (GPS-like)", 31, 55.0, 2.0056, 0.005),
        (3, "LEO debris cloud", 2100, 82.5, 14.3, 0.02),
    ]
    return {
        "schema_version": 1,
        "_note": NOTE + " Counts are for the imaginary full catalog; points are only the 10-row dummy sample.",
        "features": ["incl_deg", "log_mean_motion", "ecc"],
        "clusters": [
            {
                "label": lab,
                "name": nm,
                "count": cnt,
                "centroid": {"incl_deg": inc, "log_mean_motion": round(math.log(mm), 4), "ecc": ecc},
            }
            for lab, nm, cnt, inc, mm, ecc in defs
        ],
        "points": [
            {
                "norad_id": r["norad_id"],
                "cluster": cluster_of[r["group"]],
                "x": round(centers[cluster_of[r["group"]]][0] + rng.gauss(0, 0.8), 3),
                "y": round(centers[cluster_of[r["group"]]][1] + rng.gauss(0, 0.8), 3),
            }
            for r in cat["rows"]
        ],
    }


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    cat = catalog()
    dump("windows_dummy.json", windows())
    dump("risk_dummy.json", risk())
    dump("catalog_dummy.json", cat)
    dump("clusters_dummy.json", clusters(cat))
