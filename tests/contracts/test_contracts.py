"""Contract tests (HANDOFF.md section 7: test_contracts_validate) - owner: Yash.

a) every file in data/dummy/ (and data/results/) validates
b) windows: t_launch_unix matches t_launch_utc            (checked independently of validate.py)
c) trajectory: all arrays have equal length               (checked independently of validate.py)
d) every file has schema_version == 1
plus: the HANDOFF section 5 examples are accepted, and broken objects are rejected with
errors that name the field.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from contracts.validate import (
    KINDS,
    ContractError,
    kind_for_path,
    validate,
    validate_catalog_parquet,
    validate_file,
)

ROOT = Path(__file__).resolve().parents[2]
DUMMY_DIR = ROOT / "data" / "dummy"
RESULTS_DIR = ROOT / "data" / "results"

DUMMY_FILES = sorted(p for p in DUMMY_DIR.glob("*") if p.suffix in (".json", ".parquet"))
RESULT_FILES = sorted(p for p in RESULTS_DIR.glob("*") if p.suffix in (".json", ".parquet"))


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _json_files(kind: str):
    return [p for p in DUMMY_FILES if p.suffix == ".json" and kind_for_path(p) == kind]


# ------------------------------------------------------------------ a) dummy + result files validate


@pytest.mark.parametrize("path", DUMMY_FILES, ids=lambda p: p.name)
def test_dummy_file_validates(path):
    assert kind_for_path(path) is not None, f"{path.name}: name must start with a contract kind {KINDS}"
    validate_file(path)


@pytest.mark.parametrize("path", RESULT_FILES, ids=lambda p: p.name)
def test_result_file_validates(path):
    if kind_for_path(path) is None:
        pytest.skip(f"{path.name}: not a contract file (no kind prefix)")
    validate_file(path)


@pytest.mark.xfail(reason="rocket/trajectory/manifest_instance/result dummies are Shiva's; remove marker when landed")
def test_every_contract_has_a_dummy():
    have = {kind_for_path(p) for p in DUMMY_FILES}
    assert have >= set(KINDS), f"missing dummy files for: {sorted(set(KINDS) - have)}"


# ------------------------------------------------------------------ b) windows time consistency


@pytest.mark.parametrize("path", _json_files("windows"), ids=lambda p: p.name)
def test_windows_unix_matches_utc(path):
    obj = _load(path)
    assert obj["windows"], "dummy windows file should contain windows"
    for w in obj["windows"]:
        parsed = datetime.strptime(w["t_launch_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        assert int(parsed.timestamp()) == w["t_launch_unix"], w["id"]


def test_known_reference_time():
    # Given by the project lead; also the first dummy window.
    assert datetime.fromtimestamp(1794286000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-11-10T04:46:40Z"


# ------------------------------------------------------------------ c) trajectory array lengths


@pytest.mark.parametrize("path", _json_files("trajectory"), ids=lambda p: p.name)
def test_trajectory_arrays_equal_length(path):
    obj = _load(path)
    keys = ("t_s", "lat_deg", "lon_deg", "alt_m", "speed_mps", "q_pa", "mass_kg")
    lengths = {k: len(obj[k]) for k in keys}
    assert len(set(lengths.values())) == 1, lengths


# ------------------------------------------------------------------ d) schema_version == 1


@pytest.mark.parametrize("path", DUMMY_FILES + RESULT_FILES, ids=lambda p: p.name)
def test_schema_version_is_1(path):
    if path.suffix == ".json":
        assert _load(path).get("schema_version") == 1
    else:
        import pyarrow.parquet as pq

        meta = pq.read_schema(path).metadata or {}
        assert meta.get(b"maxq_schema_version") == b"1"


# ------------------------------------------------------------------ HANDOFF section 5 examples are accepted

EXAMPLES = {
    "windows": {
        "schema_version": 1, "profile": "crew_iss",
        "site": {"name": "Cape Canaveral", "lat_deg": 28.5, "lon_deg": -80.6},
        "target": {"norad_id": 25544, "tle_epoch_utc": "2026-11-10T00:00:00Z"},
        "generated_utc": "2026-11-10T01:00:00Z",
        "windows": [{
            "id": "w0", "t_launch_unix": 1794286000, "t_launch_utc": "2026-11-10T04:46:40Z",
            "azimuth_deg": 44.9, "plane_error_deg": 0.0, "pass": "ascending",
            "phase_angle_deg": 31.2, "rendezvous_hours_fast": 3.2, "rendezvous_hours_slow": 25.0,
            "flyable": True,
        }],
    },
    "rocket": {
        "schema_version": 1, "id": "medlift_kerolox_a", "name": "MedLift-A (fictional archetype)",
        "profile": "cargo_iss", "payload_kg": 8000,
        "stages": [
            {"dry_kg": 22000, "prop_kg": 400000, "thrust_n": 7600000, "isp_s": 282, "cd": 0.3, "area_m2": 10.8},
            {"dry_kg": 4000, "prop_kg": 110000, "thrust_n": 934000, "isp_s": 348, "cd": 0.3, "area_m2": 10.8},
        ],
    },
    "trajectory": {
        "schema_version": 1, "id": "traj_medlift_a_w0", "rocket_id": "medlift_kerolox_a",
        "window_id": "w0", "t0_unix": 1794286000, "seed": 42,
        "t_s": [0, 1, 2], "lat_deg": [28.5, 28.5, 28.5], "lon_deg": [-80.6, -80.6, -80.6],
        "alt_m": [0, 3, 12], "speed_mps": [408, 411, 420], "q_pa": [0, 20, 90],
        "mass_kg": [545000, 543900, 542800],
        "events": [{"t_s": 70, "name": "max_q"}, {"t_s": 160, "name": "meco"},
                   {"t_s": 165, "name": "stage_sep"}, {"t_s": 520, "name": "insertion"}],
        "final_orbit": {"perigee_km": 200, "apogee_km": 205, "incl_deg": 51.6, "ecc": 0.0004},
        "params": {"pitch_program": [0.0, 0.0]},
    },
    "risk": {
        "schema_version": 1, "threshold_km": 25,
        "results": [{
            "window_id": "w0", "trajectory_id": "traj_medlift_a_w0", "min_miss_km": 41.3,
            "object": {"norad_id": 12345, "name": "EXAMPLE DEB"}, "t_closest_utc": "2026-11-10T04:52:10Z",
            "risk_score": 0.12, "status": "go",
        }],
    },
    "manifest_instance": {
        "schema_version": 1, "id": "manifest_12_seed1", "pads": ["LC-A", "LC-B"], "missions": ["m0", "m1"],
        "tasks": [
            {"id": 0, "mission": "m0", "pad": "LC-A", "start_unix": 1794286000, "end_unix": 1794286600, "value": 7.5},
            {"id": 5, "mission": "m1", "pad": "LC-A", "start_unix": 1794286100, "end_unix": 1794286700, "value": 5.0},
            {"id": 9, "mission": "m1", "pad": "LC-B", "start_unix": 1794286200, "end_unix": 1794286800, "value": 6.0},
        ],
        "conflicts": [[0, 5], [0, 9]],
    },
    # HANDOFF 5.6 omits schema_version here, but section 5 says "every file has schema_version: 1".
    "result": {"schema_version": 1, "instance": "manifest_12_seed1", "solver": "sa",
               "selected": [0, 4, 9], "score": 123.4, "runtime_s": 0.8, "seed": 1},
    "clusters": {
        "schema_version": 1, "features": ["incl_deg", "log_mean_motion", "ecc"],
        "clusters": [{"label": 0, "name": "LEO shell", "count": 5400,
                      "centroid": {"incl_deg": 53.0, "log_mean_motion": 2.7, "ecc": 0.0001}}],
        "points": [{"norad_id": 1, "cluster": 0, "x": 0.0, "y": 0.0}],
    },
}


@pytest.mark.parametrize("kind", sorted(EXAMPLES))
def test_handoff_examples_validate(kind):
    validate(kind, EXAMPLES[kind])


def test_catalog_example_validates():
    row = _load(DUMMY_DIR / "catalog_dummy.json")["rows"][0] if (DUMMY_DIR / "catalog_dummy.json").exists() else None
    if row is None:
        pytest.skip("catalog dummy not present yet")
    validate("catalog", {"schema_version": 1, "rows": [row]})


# ------------------------------------------------------------------ broken objects are rejected, naming the field


def _bad(kind, mutate, expect_in_msg):
    obj = copy.deepcopy(EXAMPLES[kind])
    mutate(obj)
    with pytest.raises(ContractError) as ei:
        validate(kind, obj)
    assert expect_in_msg in str(ei.value), str(ei.value)


def test_error_names_missing_field():
    _bad("windows", lambda o: o["windows"][0].pop("azimuth_deg"), "windows[0].azimuth_deg")


def test_error_names_out_of_range_field():
    _bad("windows", lambda o: o["windows"][0].update(azimuth_deg=400), "windows[0].azimuth_deg")


def test_error_names_nested_field():
    _bad("windows", lambda o: o["site"].update(lat_deg=95), "site.lat_deg")


def test_error_names_unexpected_field():
    _bad("rocket", lambda o: o.update(colour="red"), "colour")


def test_error_names_wrong_type():
    _bad("rocket", lambda o: o["stages"][1].update(isp_s="high"), "stages[1].isp_s")


def test_schema_version_mismatch_rejected():
    _bad("risk", lambda o: o.update(schema_version=2), "schema_version")


def test_missing_schema_version_rejected():
    _bad("result", lambda o: o.pop("schema_version"), "schema_version")


def test_windows_unix_utc_mismatch_rejected():
    _bad("windows", lambda o: o["windows"][0].update(t_launch_unix=1794286001), "windows[0].t_launch_unix")


def test_windows_duplicate_ids_rejected():
    _bad("windows", lambda o: o["windows"].append(copy.deepcopy(o["windows"][0])), "duplicate window id")


def test_trajectory_unequal_lengths_rejected():
    _bad("trajectory", lambda o: o["alt_m"].append(99), "equal length")


def test_trajectory_non_monotonic_time_rejected():
    _bad("trajectory", lambda o: o.update(t_s=[0, 2, 1]), "t_s[2]")


def test_risk_bad_status_rejected():
    _bad("risk", lambda o: o["results"][0].update(status="maybe"), "results[0].status")


def test_manifest_dangling_conflict_rejected():
    _bad("manifest_instance", lambda o: o["conflicts"].append([0, 77]), "conflicts[2][1]")


def test_manifest_unknown_pad_rejected():
    _bad("manifest_instance", lambda o: o["tasks"][0].update(pad="LC-Z"), "tasks[0].pad")


def test_nan_rejected():
    _bad("rocket", lambda o: o.update(payload_kg=float("nan")), "payload_kg")


def test_unknown_kind_rejected():
    with pytest.raises(ContractError, match="unknown contract kind"):
        validate("nope", {})


def test_non_object_rejected():
    with pytest.raises(ContractError, match="expected a JSON object"):
        validate("windows", [])


def test_all_errors_reported_together():
    obj = copy.deepcopy(EXAMPLES["windows"])
    obj["windows"][0]["azimuth_deg"] = -1
    obj["site"]["lat_deg"] = 91
    with pytest.raises(ContractError) as ei:
        validate("windows", obj)
    assert len(ei.value.errors) == 2


# ------------------------------------------------------------------ catalog as a table


def _catalog_rows():
    path = DUMMY_DIR / "catalog_dummy.json"
    if not path.exists():
        pytest.skip("catalog dummy not present yet")
    return _load(path)["rows"]


def test_catalog_dataframe_validates():
    import pandas as pd

    validate("catalog", pd.DataFrame(_catalog_rows()))


def test_catalog_dataframe_wrong_dtype_names_column():
    import pandas as pd

    df = pd.DataFrame(_catalog_rows())
    df["incl_deg"] = df["incl_deg"].astype(str)
    with pytest.raises(ContractError, match="incl_deg: dtype"):
        validate("catalog", df)


def test_catalog_dataframe_missing_column_names_column():
    import pandas as pd

    df = pd.DataFrame(_catalog_rows()).drop(columns=["raan_deg"])
    with pytest.raises(ContractError, match="raan_deg: required column is missing"):
        validate("catalog", df)


def test_catalog_dataframe_bad_value_names_row_and_field():
    import pandas as pd

    df = pd.DataFrame(_catalog_rows())
    df.loc[3, "ecc"] = 1.5
    with pytest.raises(ContractError, match=r"rows\[3\]\.ecc"):
        validate("catalog", df)


def test_catalog_dataframe_nan_rejected():
    import pandas as pd

    df = pd.DataFrame(_catalog_rows())
    df.loc[2, "alt_km_approx"] = float("nan")
    with pytest.raises(ContractError, match="alt_km_approx: non-finite"):
        validate("catalog", df)


def test_catalog_parquet_requires_schema_version_metadata(tmp_path):
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pandas(pd.DataFrame(_catalog_rows()), preserve_index=False)
    bad = tmp_path / "bad.parquet"
    pq.write_table(table, bad)
    with pytest.raises(ContractError, match="maxq_schema_version"):
        validate_catalog_parquet(bad)
    good = tmp_path / "catalog.parquet"
    pq.write_table(table.replace_schema_metadata({"maxq_schema_version": "1"}), good)
    validate_catalog_parquet(good)


def test_catalog_tle_must_match_norad_id():
    rows = copy.deepcopy(_catalog_rows())
    rows[0]["norad_id"] = 12345
    with pytest.raises(ContractError, match=r"rows\[0\]\.tle1"):
        validate("catalog", {"schema_version": 1, "rows": rows})
