"""OMM path: complete catalog including objects numbered >= 100000 (no TLE). Owner: Yash.

Ground truth: real CelesTrak OMM records paired with CelesTrak's own TLE text for the same element set (fetched
2026-11-09), so the TLE renderer is checked against the publisher's output, not against itself.
"""

from __future__ import annotations

import copy

import pandas as pd
import pytest
from sgp4.api import Satrec

from catalog.fetch import build_catalog, get_row, get_tle, sources_from_sample
from catalog.omm import (
    OmmError,
    has_omm,
    omm_to_row,
    omm_to_tle,
    positions_agree,
    row_to_tle,
    satrec_from_row,
    tle_checksum_ok,
)
from contracts.validate import ContractError, validate

# (OMM JSON record, CelesTrak's TLE lines for the same element set)
REAL_PAIRS = [
    (
        {"OBJECT_NAME": "UOSAT 2 (UO-11)", "OBJECT_ID": "1984-021B", "EPOCH": "2026-11-09T04:25:56.028864",
         "MEAN_MOTION": 14.9112343, "ECCENTRICITY": 0.00090235, "INCLINATION": 97.8275, "RA_OF_ASC_NODE": 277.3336,
         "ARG_OF_PERICENTER": 68.491, "MEAN_ANOMALY": 291.727, "EPHEMERIS_TYPE": 0, "CLASSIFICATION_TYPE": "U",
         "NORAD_CAT_ID": 14781, "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 27873, "BSTAR": 0.00020306267,
         "MEAN_MOTION_DOT": 1.958e-05, "MEAN_MOTION_DDOT": 0},
        "1 14781U 84021B   26313.18467626  .00001958  00000+0  20306-3 0  9997",
        "2 14781  97.8275 277.3336 0009023  68.4910 291.7270 14.91123430278737",
    ),
    (
        {"OBJECT_NAME": "STARLINK-11174 [DTC]", "OBJECT_ID": "2024-141M", "EPOCH": "2026-11-09T07:24:08.476416",
         "MEAN_MOTION": 15.6969812, "ECCENTRICITY": 9.569e-05, "INCLINATION": 53.1588, "RA_OF_ASC_NODE": 320.3352,
         "ARG_OF_PERICENTER": 86.0159, "MEAN_ANOMALY": 274.0968, "EPHEMERIS_TYPE": 0, "CLASSIFICATION_TYPE": "U",
         "NORAD_CAT_ID": 60409, "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 13011, "BSTAR": 9.69705e-06,
         "MEAN_MOTION_DOT": 5.86e-06, "MEAN_MOTION_DDOT": 0},
        "1 60409U 24141M   26313.30843144  .00000586  00000+0  96971-5 0  9998",
        "2 60409  53.1588 320.3352 0000956  86.0159 274.0968 15.69698120130117",
    ),
]  # fmt: skip

# Real record of a visiting vehicle with a 6-digit catalog number (Soyuz MS-29, docked to the ISS): no TLE exists.
SOYUZ_OMM = {
    "OBJECT_NAME": "SOYUZ-MS 29", "OBJECT_ID": "2026-162A", "EPOCH": "2026-11-09T11:10:18.771456",
    "MEAN_MOTION": 15.49275323, "ECCENTRICITY": 0.00072858, "INCLINATION": 51.6321, "RA_OF_ASC_NODE": 301.1765,
    "ARG_OF_PERICENTER": 6.3021, "MEAN_ANOMALY": 353.8058, "EPHEMERIS_TYPE": 0, "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 100057, "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 59446, "BSTAR": 0.00016254709,
    "MEAN_MOTION_DOT": 8.605e-05, "MEAN_MOTION_DDOT": 0,
}  # fmt: skip


@pytest.mark.parametrize(
    ("omm", "tle1", "tle2"), REAL_PAIRS, ids=lambda x: x["NORAD_CAT_ID"] if isinstance(x, dict) else ""
)
def test_rendered_tle_equals_celestraks_own_text(omm, tle1, tle2):
    assert omm_to_tle(omm) == (tle1, tle2)
    assert tle_checksum_ok(tle1) and tle_checksum_ok(tle2)


@pytest.mark.parametrize(
    ("omm", "tle1", "tle2"), REAL_PAIRS, ids=lambda x: x["NORAD_CAT_ID"] if isinstance(x, dict) else ""
)
def test_row_has_tle_and_omm_columns_and_propagates_like_the_tle(omm, tle1, tle2):
    row = omm_to_row(omm)
    assert (row["tle1"], row["tle2"]) == (tle1, tle2) and has_omm(row)
    assert positions_agree(satrec_from_row(row), Satrec.twoline2rv(tle1, tle2), days=1.0, tol_km=1.0)


def test_six_digit_object_has_no_tle_but_propagates():
    row = omm_to_row(SOYUZ_OMM)
    assert row["tle1"] is None and row["tle2"] is None and row["norad_id"] == 100057
    sat = satrec_from_row(row)
    assert sat.satnum == 100057
    err, r, _ = sat.sgp4(sat.jdsatepoch, sat.jdsatepochF)
    assert err == 0 and 6600 < (r[0] ** 2 + r[1] ** 2 + r[2] ** 2) ** 0.5 < 6900  # LEO radius, km


def test_placeholder_tle_for_six_digit_object_is_propagation_equivalent():
    row = omm_to_row(SOYUZ_OMM)
    t1, t2 = row_to_tle(row, satnum=100057 % 100000)
    assert t1[2:7] == "00057" and tle_checksum_ok(t1) and tle_checksum_ok(t2)
    assert positions_agree(satrec_from_row(row), Satrec.twoline2rv(t1, t2), days=1.0, tol_km=1.0)
    with pytest.raises(OmmError, match="does not fit a TLE"):
        omm_to_tle(SOYUZ_OMM)  # a real 6-digit number can never be printed in a TLE


def test_bad_records_are_rejected_loudly():
    with pytest.raises(OmmError, match="missing"):
        omm_to_row({k: v for k, v in SOYUZ_OMM.items() if k != "BSTAR"})
    with pytest.raises(OmmError, match="unphysical"):
        omm_to_row({**SOYUZ_OMM, "ECCENTRICITY": 1.2})
    with pytest.raises(OmmError):
        omm_to_row({**SOYUZ_OMM, "EPOCH": "yesterday"})


# ------------------------------------------------------------------ the contract accepts / rejects the new shapes


@pytest.fixture(scope="module")
def sample_df():
    return build_catalog(sources_from_sample()[0])[0]


def test_sample_catalog_is_complete_and_contract_valid(sample_df):
    validate("catalog", sample_df)
    six = sample_df[sample_df["norad_id"] >= 100000]
    assert (
        len(six) >= 4 and six["tle1"].isna().all() and sample_df[sample_df["norad_id"] < 100000]["tle1"].notna().all()
    )
    assert {"SOYUZ-MS 29", "CREW DRAGON 13"} <= set(six["name"])  # the ISS visiting vehicles


def test_every_sample_object_can_be_propagated(sample_df):
    for _, r in sample_df.iterrows():
        sat = satrec_from_row(r)
        err, _, _ = sat.sgp4(sat.jdsatepoch, sat.jdsatepochF)
        assert err == 0, r["name"]


def test_get_tle_refuses_six_digit_objects_with_a_pointer(sample_df):
    with pytest.raises(ValueError, match="satrec_from_row"):
        get_tle(sample_df, 100057)
    assert get_row(sample_df, 100057)["name"] == "SOYUZ-MS 29"


def test_contract_rejects_null_tle_for_a_five_digit_object(sample_df):
    df = sample_df.copy()
    i = df.index[df["norad_id"] == 25544][0]
    df.loc[i, "tle1"] = None
    with pytest.raises(ContractError, match=r"rows\[\d+\]\.tle1: null, but norad_id 25544"):
        validate("catalog", df)


def test_contract_requires_omm_columns_when_tle_is_null(sample_df):
    obj = {"schema_version": 1, "rows": [{k: v for k, v in omm_to_row(SOYUZ_OMM).items() if k != "argp_deg"}]}
    obj["rows"][0]["group"] = "stations"
    with pytest.raises(ContractError, match="requires the OMM columns"):
        validate("catalog", obj)


def test_old_style_catalog_without_omm_columns_still_validates():
    dummy = __import__("json").load(open("data/dummy/catalog_dummy.json", encoding="utf-8"))
    validate("catalog", dummy)  # additive change: the committed dummy has no OMM columns
    validate("catalog", pd.DataFrame(dummy["rows"]))


def test_row_is_not_mutated_by_rendering():
    row = omm_to_row(copy.deepcopy(SOYUZ_OMM))
    before = dict(row)
    row_to_tle(row, satnum=57)
    assert row == before
