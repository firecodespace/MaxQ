"""MaxQ data-contract validator.

Usage from Python::

    from contracts.validate import validate
    validate("windows", obj)          # returns obj, raises ContractError

Usage from the shell (defaults to data/dummy and data/results)::

    python -m contracts.validate [file ...]

Every error message names the offending field (``windows[0].azimuth_deg``).
Beyond JSON Schema, a few cross-field rules are enforced because the schema
language cannot express them (equal array lengths, UTC string == unix time,
referential integrity in manifest instances, ...). These are part of the
contract.

Owner: Yash (contracts are gated; see AGENTS.md section 6).
"""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

CONTRACTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = CONTRACTS_DIR.parent
SCHEMA_VERSION = 1

KINDS = (
    "catalog",
    "windows",
    "rocket",
    "trajectory",
    "risk",
    "manifest_instance",
    "result",
    "clusters",
)

_TRAJECTORY_ARRAYS = ("t_s", "lat_deg", "lon_deg", "alt_m", "speed_mps", "q_pa", "mass_kg")
_PARQUET_VERSION_KEY = b"maxq_schema_version"
_MAX_MSG = 300


class ContractError(ValueError):
    """A contract violation. ``errors`` lists every problem found."""

    def __init__(self, kind: str, errors: list[str]):
        self.kind = kind
        self.errors = errors
        shown = "\n  - ".join(errors[:20])
        more = f"\n  ... and {len(errors) - 20} more" if len(errors) > 20 else ""
        super().__init__(f"{kind}: {len(errors)} contract violation(s):\n  - {shown}{more}")


# --------------------------------------------------------------------------- schemas


_schema_cache: dict[str, dict] = {}
_validator_cache: dict[str, Draft202012Validator] = {}


def load_schema(kind: str) -> dict:
    if kind not in KINDS:
        raise ContractError(kind, [f"unknown contract kind {kind!r}; expected one of {list(KINDS)}"])
    if kind not in _schema_cache:
        path = CONTRACTS_DIR / f"{kind}.schema.json"
        _schema_cache[kind] = json.loads(path.read_text(encoding="utf-8"))
    return _schema_cache[kind]


def _validator(kind: str) -> Draft202012Validator:
    if kind not in _validator_cache:
        schema = load_schema(kind)
        Draft202012Validator.check_schema(schema)
        _validator_cache[kind] = Draft202012Validator(schema)
    return _validator_cache[kind]


# --------------------------------------------------------------------------- helpers


def _fmt_path(parts) -> str:
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out or "<root>"


def _clip(msg: str) -> str:
    return msg if len(msg) <= _MAX_MSG else msg[: _MAX_MSG - 3] + "..."


def _schema_errors(kind: str, obj: Any) -> list[str]:
    errors = []
    for e in sorted(_validator(kind).iter_errors(obj), key=lambda e: [str(p) for p in e.absolute_path]):
        path = list(e.absolute_path)
        if e.validator == "required":
            missing = [k for k in e.validator_value if isinstance(e.instance, dict) and k not in e.instance]
            for k in missing:
                errors.append(f"{_fmt_path(path + [k])}: required field is missing")
            continue
        errors.append(_clip(f"{_fmt_path(path)}: {e.message}"))
    return errors


def _nonfinite(obj: Any, path: list, out: list[str]) -> None:
    if isinstance(obj, float):
        if not math.isfinite(obj):
            out.append(f"{_fmt_path(path)}: non-finite number ({obj})")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _nonfinite(v, path + [k], out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _nonfinite(v, path + [i], out)


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def _utc_to_unix(s: str) -> float:
    return _parse_utc(s).timestamp()


# --------------------------------------------------------------------------- semantic rules


def _check_windows(obj: dict) -> list[str]:
    errs = []
    seen = set()
    for i, w in enumerate(obj["windows"]):
        p = f"windows[{i}]"
        try:
            expect = _utc_to_unix(w["t_launch_utc"])
        except ValueError:
            errs.append(f"{p}.t_launch_utc: not a valid UTC timestamp ({w['t_launch_utc']!r})")
        else:
            if abs(expect - w["t_launch_unix"]) > 0.5:
                errs.append(
                    f"{p}.t_launch_unix: {w['t_launch_unix']} does not match t_launch_utc "
                    f"{w['t_launch_utc']!r} (which is {expect:.0f})"
                )
        if w["id"] in seen:
            errs.append(f"{p}.id: duplicate window id {w['id']!r}")
        seen.add(w["id"])
    return errs


def _check_trajectory(obj: dict) -> list[str]:
    errs = []
    lengths = {k: len(obj[k]) for k in _TRAJECTORY_ARRAYS}
    if len(set(lengths.values())) > 1:
        errs.append(f"trajectory arrays must all have equal length, got {lengths}")
    t = obj["t_s"]
    for i in range(1, len(t)):
        if not t[i] > t[i - 1]:
            errs.append(f"t_s[{i}]: not strictly increasing ({t[i - 1]} -> {t[i]})")
            break
    return errs


def _check_manifest_instance(obj: dict) -> list[str]:
    errs = []
    ids = set()
    for i, t in enumerate(obj["tasks"]):
        p = f"tasks[{i}]"
        if t["id"] in ids:
            errs.append(f"{p}.id: duplicate task id {t['id']}")
        ids.add(t["id"])
        if t["end_unix"] <= t["start_unix"]:
            errs.append(f"{p}.end_unix: must be after start_unix ({t['start_unix']} .. {t['end_unix']})")
        if t["pad"] not in obj["pads"]:
            errs.append(f"{p}.pad: {t['pad']!r} not in pads {obj['pads']}")
        if t["mission"] not in obj["missions"]:
            errs.append(f"{p}.mission: {t['mission']!r} not in missions {obj['missions']}")
    for i, (a, b) in enumerate(obj["conflicts"]):
        for j, tid in enumerate((a, b)):
            if tid not in ids:
                errs.append(f"conflicts[{i}][{j}]: task id {tid} does not exist")
        if a == b:
            errs.append(f"conflicts[{i}]: task {a} conflicts with itself")
    return errs


def _check_clusters(obj: dict) -> list[str]:
    errs = []
    labels = {c["label"] for c in obj["clusters"]}
    if len(labels) != len(obj["clusters"]):
        errs.append("clusters: duplicate cluster label")
    feats = set(obj["features"])
    for i, c in enumerate(obj["clusters"]):
        if c["centroid"] and set(c["centroid"]) != feats:
            errs.append(f"clusters[{i}].centroid: keys {sorted(c['centroid'])} != features {sorted(feats)}")
    for i, pt in enumerate(obj["points"]):
        if pt["cluster"] not in labels:
            errs.append(f"points[{i}].cluster: label {pt['cluster']} not defined in clusters")
    cg = obj.get("congestion")
    if cg:
        n = len(cg["counts"])
        if len(cg["altitude_edges_km"]) != n + 1:
            errs.append(
                f"congestion.altitude_edges_km: need len(counts)+1 = {n + 1} edges, got {len(cg['altitude_edges_km'])}"
            )
        if len(cg["density_per_million_km3"]) != n:
            errs.append(
                f"congestion.density_per_million_km3: need {n} values, got {len(cg['density_per_million_km3'])}"
            )
        e = cg["altitude_edges_km"]
        if any(b <= a for a, b in zip(e, e[1:], strict=False)):
            errs.append("congestion.altitude_edges_km: must be strictly increasing")
    return errs


def _check_catalog(obj: dict) -> list[str]:
    errs = []
    for i, r in enumerate(obj["rows"]):
        six = r["norad_id"] >= 100000  # 6-digit ids do not fit classic TLE columns
        for line in ("tle1", "tle2"):
            if r[line] is None:
                if not six:
                    errs.append(
                        f"rows[{i}].{line}: null, but norad_id {r['norad_id']} has a 5-digit TLE (null only >= 100000)"
                    )
                elif "argp_deg" not in r:
                    errs.append(
                        f"rows[{i}].{line}: null requires the OMM columns (argp_deg, ...) to propagate the object"
                    )
            elif not six and r[line][2:7] != f"{r['norad_id']:05d}":
                errs.append(f"rows[{i}].{line}: catalog number {r[line][2:7]!r} != norad_id {r['norad_id']}")
    return errs


_SEMANTIC = {
    "windows": _check_windows,
    "trajectory": _check_trajectory,
    "manifest_instance": _check_manifest_instance,
    "clusters": _check_clusters,
    "catalog": _check_catalog,
}


# --------------------------------------------------------------------------- catalog tables


def _is_table(obj: Any) -> bool:
    return type(obj).__module__.split(".")[0] in ("pandas", "pyarrow") and not isinstance(obj, dict)


def _catalog_from_table(obj: Any) -> dict:
    """Convert a DataFrame / pyarrow Table into the JSON form, after dtype checks."""
    import numpy as np
    import pandas as pd

    kind = "catalog"
    spec = load_schema(kind)["x-columns"]
    if type(obj).__module__.startswith("pyarrow"):
        meta = (obj.schema.metadata or {}).get(_PARQUET_VERSION_KEY)
        if meta != str(SCHEMA_VERSION).encode():
            raise ContractError(
                kind,
                [f"schema_version: pyarrow table metadata {_PARQUET_VERSION_KEY.decode()!r} is {meta!r}, expected '1'"],
            )
        obj = obj.to_pandas()
    df = obj
    errs = []
    want = [c["name"] for c in spec]
    required = [c["name"] for c in spec if not c.get("optional")]
    missing = [c for c in required if c not in df.columns]
    extra = [c for c in df.columns if c not in want]
    errs += [f"{c}: required column is missing" for c in missing]
    errs += [f"{c}: unexpected column (not in catalog.schema.json)" for c in extra]
    for c in spec:
        if c["name"] not in df.columns:
            continue
        s = df[c["name"]]
        ok = {
            "int": pd.api.types.is_integer_dtype,
            "float": pd.api.types.is_float_dtype,
            "str": lambda x: pd.api.types.is_string_dtype(x) or pd.api.types.is_object_dtype(x),
        }[c["dtype"]](s)
        if not ok:
            errs.append(f"{c['name']}: dtype {s.dtype} is not {c['dtype']}")
        elif c.get("nullable"):
            continue  # nulls allowed (value rules, e.g. "null only for norad_id >= 100000", are semantic checks)
        elif c["dtype"] == "float" and not np.isfinite(s.to_numpy(dtype=float)).all():
            bad = int(np.flatnonzero(~np.isfinite(s.to_numpy(dtype=float)))[0])
            errs.append(f"{c['name']}: non-finite value at row {bad}")
        elif s.isna().any():
            errs.append(f"{c['name']}: null value at row {int(np.flatnonzero(s.isna().to_numpy())[0])}")
    if errs:
        raise ContractError(kind, errs)
    present = [c["name"] for c in spec if c["name"] in df.columns]
    nullable = {c["name"] for c in spec if c.get("nullable")}
    records = df[present].to_dict("records")
    for r in records:  # pandas gives NaN for missing strings: normalise to None so the schema sees a real null
        for k in nullable:
            if k in r and r[k] is not None and r[k] != r[k]:
                r[k] = None
    return {"schema_version": SCHEMA_VERSION, "rows": records}


# --------------------------------------------------------------------------- public API


def validate(kind: str, obj: Any) -> Any:
    """Validate ``obj`` against contract ``kind``. Returns ``obj`` or raises ContractError.

    ``catalog`` also accepts a pandas DataFrame or pyarrow Table.
    """
    load_schema(kind)  # unknown kind -> ContractError
    target = obj
    if kind == "catalog" and _is_table(obj):
        target = _catalog_from_table(obj)
    elif not isinstance(obj, dict):
        raise ContractError(kind, [f"<root>: expected a JSON object, got {type(obj).__name__}"])

    errs: list[str] = []
    if target is obj:  # tables were already checked for non-finite values column-wise
        _nonfinite(target, [], errs)
    errs += _schema_errors(kind, target)
    if not errs and kind in _SEMANTIC:
        errs += _SEMANTIC[kind](target)
    if errs:
        raise ContractError(kind, errs)
    return obj


def kind_for_path(path: str | Path) -> str | None:
    """Infer the contract kind from a file name such as ``windows_dummy.json``."""
    stem = Path(path).stem.lower()
    if stem.endswith("_dummy"):
        stem = stem[: -len("_dummy")]
    for kind in sorted(KINDS, key=len, reverse=True):
        if stem == kind or stem.startswith(kind + "_"):
            return kind
    return None


def _reject_constant(name: str):
    raise ValueError(f"non-finite JSON constant {name} is not allowed")


def load_json(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f, parse_constant=_reject_constant)


def validate_catalog_parquet(path: str | Path) -> None:
    """Validate a catalog.parquet file, including its schema_version file metadata."""
    import pyarrow.parquet as pq

    validate("catalog", pq.read_table(path))


def validate_file(path: str | Path, kind: str | None = None) -> str:
    """Validate a file on disk; returns the kind used."""
    path = Path(path)
    kind = kind or kind_for_path(path)
    if kind is None:
        raise ContractError("?", [f"{path.name}: cannot infer contract kind from file name; pass kind="])
    try:
        if path.suffix == ".parquet":
            if kind != "catalog":
                raise ContractError(kind, [f"{path.name}: only the catalog contract is a parquet file"])
            validate_catalog_parquet(path)
        else:
            validate(kind, load_json(path))
    except ContractError as e:
        raise ContractError(kind, [f"{path.name}: {m}" for m in e.errors]) from None
    except ValueError as e:  # bad JSON
        raise ContractError(kind, [f"{path.name}: cannot parse: {e}"]) from None
    return kind


def default_files() -> list[Path]:
    files: list[Path] = []
    for d in ("data/dummy", "data/results"):
        files += sorted(p for p in (REPO_ROOT / d).glob("*") if p.suffix in (".json", ".parquet"))
    return files


def main(argv: list[str] | None = None) -> int:
    paths = [Path(a) for a in (argv if argv is not None else sys.argv[1:])] or default_files()
    failed = 0
    for p in paths:
        try:
            kind = validate_file(p)
            print(f"ok    {p.name}  ({kind})")
        except ContractError as e:
            failed += 1
            print(f"FAIL  {p.name}\n{e}")
    print(f"{len(paths) - failed}/{len(paths)} valid")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
