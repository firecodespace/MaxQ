"""CelesTrak OMM (JSON) <-> catalog rows <-> SGP4 satellites <-> TLE text.

Why OMM: the 5-digit catalog-number space ran out on 2026-07-11, so objects numbered >= 100000 have no TLE. CelesTrak's
JSON (OMM) format carries the full SGP4 element set for every object, and sgp4 can propagate it directly, so the catalog
is complete. For 5-digit objects a TLE is still rendered (contract fields tle1/tle2, used by the browser's satellite.js,
which has no OMM support in the vendored 5.0.0); for >= 100000 the contract's tle1/tle2 are null, and the web export
uses `row_to_tle(row, satnum=...)` with a placeholder catalog number purely for propagation.

TLE rendering is lossy by design (TLE eccentricity has 7 digits, OMM has 8), so SGP4 for the catalog always uses the
OMM columns when they are present (`satrec_from_row`).

Owner: Yash (lane: catalog/).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
from sgp4 import omm as sgp4_omm
from sgp4.api import Satrec

from core.constants import MU_M3_S2, R_E_M

OMM_REQUIRED = (
    "OBJECT_NAME", "OBJECT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE",
    "ARG_OF_PERICENTER", "MEAN_ANOMALY", "EPHEMERIS_TYPE", "CLASSIFICATION_TYPE", "NORAD_CAT_ID",
    "ELEMENT_SET_NO", "REV_AT_EPOCH", "BSTAR", "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT",
)  # fmt: skip

OMM_COLUMNS = (
    "intl_designator", "classification", "ephemeris_type", "elset_no", "rev_at_epoch", "argp_deg",
    "mean_anomaly_deg", "bstar", "mean_motion_dot", "mean_motion_ddot",
)  # fmt: skip


class OmmError(ValueError):
    pass


# --------------------------------------------------------------------------- OMM record -> catalog row


def omm_to_row(omm: dict) -> dict:
    """One CelesTrak OMM JSON record -> catalog row (without ``group``). Raises OmmError on a bad record."""
    missing = [k for k in OMM_REQUIRED if k not in omm or omm[k] is None]
    if missing:
        raise OmmError(f"OMM record missing {missing}")
    try:
        norad = int(omm["NORAD_CAT_ID"])
        n_rev = float(omm["MEAN_MOTION"])
        ecc = float(omm["ECCENTRICITY"])
        a_m = (MU_M3_S2 / (n_rev * 2.0 * math.pi / 86400.0) ** 2) ** (1.0 / 3.0)
        epoch = str(omm["EPOCH"])
        datetime.strptime(epoch, "%Y-%m-%dT%H:%M:%S.%f")  # the format sgp4.omm requires (fractional seconds)
        row = {
            "norad_id": norad,
            "name": str(omm["OBJECT_NAME"]).strip(),
            "tle1": None,
            "tle2": None,
            "epoch_utc": epoch + "Z",
            "incl_deg": float(omm["INCLINATION"]),
            "ecc": ecc,
            "mean_motion_rev_day": n_rev,
            "raan_deg": float(omm["RA_OF_ASC_NODE"]),
            "alt_km_approx": round((a_m - R_E_M) / 1000.0, 3),
            "intl_designator": str(omm["OBJECT_ID"]),
            "classification": str(omm["CLASSIFICATION_TYPE"]),
            "ephemeris_type": int(omm["EPHEMERIS_TYPE"]),
            "elset_no": int(omm["ELEMENT_SET_NO"]),
            "rev_at_epoch": int(omm["REV_AT_EPOCH"]),
            "argp_deg": float(omm["ARG_OF_PERICENTER"]),
            "mean_anomaly_deg": float(omm["MEAN_ANOMALY"]),
            "bstar": float(omm["BSTAR"]),
            "mean_motion_dot": float(omm["MEAN_MOTION_DOT"]),
            "mean_motion_ddot": float(omm["MEAN_MOTION_DDOT"]),
        }
    except (ValueError, TypeError, ZeroDivisionError) as e:
        raise OmmError(f"bad OMM record for {omm.get('NORAD_CAT_ID')}: {e}") from None
    if not (n_rev > 0 and 0 <= ecc < 1):
        raise OmmError(f"unphysical elements for {norad}: n={n_rev} e={ecc}")
    if norad < 100000:
        row["tle1"], row["tle2"] = omm_to_tle(omm)
    return row


# --------------------------------------------------------------------------- row -> satellite


def row_to_omm(row) -> dict:
    """Catalog row (dict/Series) -> OMM field dict understood by ``sgp4.omm.initialize``."""
    return {
        "OBJECT_ID": row["intl_designator"],
        "EPOCH": str(row["epoch_utc"]).rstrip("Z"),
        "MEAN_MOTION": row["mean_motion_rev_day"],
        "ECCENTRICITY": row["ecc"],
        "INCLINATION": row["incl_deg"],
        "RA_OF_ASC_NODE": row["raan_deg"],
        "ARG_OF_PERICENTER": row["argp_deg"],
        "MEAN_ANOMALY": row["mean_anomaly_deg"],
        "EPHEMERIS_TYPE": int(row["ephemeris_type"]),
        "CLASSIFICATION_TYPE": row["classification"],
        "NORAD_CAT_ID": int(row["norad_id"]),
        "ELEMENT_SET_NO": int(row["elset_no"]),
        "REV_AT_EPOCH": int(row["rev_at_epoch"]),
        "BSTAR": row["bstar"],
        "MEAN_MOTION_DOT": row["mean_motion_dot"],
        "MEAN_MOTION_DDOT": row["mean_motion_ddot"],
    }


def is_missing(v) -> bool:
    """None or NaN (pandas turns a missing string in an object column into NaN)."""
    return v is None or (isinstance(v, float) and v != v)


def has_omm(row) -> bool:
    try:
        return not any(is_missing(row[c]) for c in OMM_COLUMNS)
    except KeyError:
        return False


def satrec_from_row(row) -> Satrec:
    """SGP4 satellite for a catalog row: from the OMM columns when present (all objects), else from the TLE."""
    if has_omm(row):
        sat = Satrec()
        sgp4_omm.initialize(sat, row_to_omm(row))
        return sat
    if is_missing(row["tle1"]) or is_missing(row["tle2"]):
        raise OmmError(f"NORAD {row['norad_id']} has neither OMM columns nor a TLE: cannot propagate")
    return Satrec.twoline2rv(row["tle1"], row["tle2"])


# --------------------------------------------------------------------------- TLE rendering


def tle_checksum(line: str) -> int:
    return sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line[:68]) % 10


def tle_checksum_ok(line: str) -> bool:
    return len(line) == 69 and line[68].isdigit() and tle_checksum(line) == int(line[68])


def _exp_field(v: float) -> str:
    """8-char TLE implied-decimal exponent field, e.g. 0.00016254709 -> ' 16255-3'; 0 -> ' 00000+0'."""
    if v == 0.0:
        return " 00000+0"
    sign = "-" if v < 0 else " "
    e = math.floor(math.log10(abs(v))) + 1
    digits = math.floor(abs(v) / 10.0**e * 1e5 + 0.5 + 1e-6)  # round half up, like CelesTrak
    if digits >= 100000:
        digits //= 10
        e += 1
    return f"{sign}{digits:05d}{'-' if e < 0 else '+'}{abs(e):d}"


def _ndot_field(v: float) -> str:
    s = f"{abs(v):.8f}".lstrip("0") or ".00000000"
    return f"{'-' if v < 0 else ' '}{s}"


def omm_to_tle(omm: dict, satnum: int | None = None) -> tuple[str, str]:
    """Render a TLE from an OMM record. ``satnum`` overrides the printed catalog number (must fit 5 digits)."""
    norad = int(omm["NORAD_CAT_ID"]) if satnum is None else int(satnum)
    if not 0 <= norad <= 99999:
        raise OmmError(f"catalog number {norad} does not fit a TLE (use satnum= for a placeholder)")
    ep = datetime.strptime(str(omm["EPOCH"]), "%Y-%m-%dT%H:%M:%S.%f")
    day = (ep - datetime(ep.year, 1, 1)) / timedelta(days=1) + 1.0
    oid = str(omm["OBJECT_ID"])  # '1998-067A' -> '98067A'
    intl = (oid[2:4] + oid[5:]) if len(oid) >= 8 and oid[4] == "-" else oid[:8]
    l1 = (
        f"1 {norad:05d}{omm['CLASSIFICATION_TYPE'][:1]} {intl:<8s} {ep.year % 100:02d}{day:012.8f} "
        f"{_ndot_field(float(omm['MEAN_MOTION_DOT']))} {_exp_field(float(omm['MEAN_MOTION_DDOT']))} "
        f"{_exp_field(float(omm['BSTAR']))} {int(omm['EPHEMERIS_TYPE'])} {int(omm['ELEMENT_SET_NO']) % 10000:4d}"
    )
    ecc7 = min(math.floor(float(omm["ECCENTRICITY"]) * 1e7 + 1e-6), 9999999)  # CelesTrak truncates, not rounds
    l2 = (
        f"2 {norad:05d} {float(omm['INCLINATION']):8.4f} {float(omm['RA_OF_ASC_NODE']):8.4f} {ecc7:07d} "
        f"{float(omm['ARG_OF_PERICENTER']):8.4f} {float(omm['MEAN_ANOMALY']):8.4f} "
        f"{float(omm['MEAN_MOTION']):11.8f}{int(omm['REV_AT_EPOCH']) % 100000:5d}"
    )
    if len(l1) != 68 or len(l2) != 68:
        raise OmmError(f"TLE rendering produced lines of {len(l1)}/{len(l2)} chars for NORAD {norad}")
    return l1 + str(tle_checksum(l1)), l2 + str(tle_checksum(l2))


def row_to_tle(row, satnum: int | None = None) -> tuple[str, str]:
    """TLE lines for a catalog row. With ``satnum`` the OMM columns are re-rendered under that placeholder number
    (for propagation-only use of objects >= 100000); otherwise the row's own TLE is returned (None for >= 100000)."""
    if satnum is None and not is_missing(row["tle1"]):
        return row["tle1"], row["tle2"]
    omm = row_to_omm(row)
    return omm_to_tle(omm, satnum=satnum if satnum is not None else int(row["norad_id"]) % 100000)


def rows_to_satrecs(df, idx=None) -> list[Satrec]:
    rows = df.iloc[idx] if idx is not None else df
    return [satrec_from_row(r) for _, r in rows.iterrows()]


def positions_agree(a: Satrec, b: Satrec, days: float = 1.0, tol_km: float = 1.0) -> bool:
    """Helper for tests/diagnostics: do two satellites agree to ``tol_km`` over ``days`` from a's epoch?"""
    jd = a.jdsatepoch + np.floor(a.jdsatepochF)
    fr = a.jdsatepochF - np.floor(a.jdsatepochF) + np.linspace(0.0, days, 25)
    ea, ra, _ = a.sgp4_array(np.full_like(fr, jd), fr)
    eb, rb, _ = b.sgp4_array(np.full_like(fr, jd), fr)
    return bool((ea == 0).all() and (eb == 0).all() and np.linalg.norm(ra - rb, axis=1).max() < tol_km)
