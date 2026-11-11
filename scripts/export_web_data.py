"""Export validated data for the static site into web/public/data/.

    python scripts/export_web_data.py [--out web/public/data] [--all]

* Copies every contract-valid JSON in data/dummy/ (labelled dummy) and data/results/ (real) and writes
  ``manifest.json`` listing them, so the page knows what exists and what is dummy (it degrades gracefully
  when a file is missing).
* Writes ``catalog_web.json``: compact TLEs for the live-satellite layer. By default a deterministic
  per-group subsample keeps the file near 1.2 MB (the repo is not a data store); ``--all`` writes everything
  (git-ignored by default only if you point --out elsewhere).
* Invalid files fail the export loudly (contracts are validated at read).

Owner: Yash (lane: scripts/, web/).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ascent.rockets import Rocket, list_archetypes  # noqa: E402
from ascent.sim import DEFAULT_PITCH_PARAMS  # noqa: E402
from catalog.fetch import OUT_PATH, build_catalog, load_catalog, sources_from_sample  # noqa: E402
from catalog.omm import is_missing, row_to_tle  # noqa: E402
from contracts.validate import ContractError, kind_for_path, validate, validate_file  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "web" / "public" / "data"
DATA_DIRS = (REPO_ROOT / "data" / "dummy", REPO_ROOT / "data" / "results")
EXPORT_KINDS = {"windows", "trajectory", "risk", "clusters", "rocket", "result", "manifest_instance"}
# Which archetype's tuned pitch program a design-search rocket borrows, by mission profile.
PROFILE_ARCHETYPE = {
    "cargo_iss": "medlift_kerolox_a",
    "crew_iss": "crewlift_kerolox_b",
    "geo_comm": "heavylift_hydrolox_c",
    "meo_gps": "heavylift_hydrolox_c",
}
GROUP_ORDER = ["stations", "gps-ops", "geo", "active", "starlink", "debris"]
# Per-group caps for the default (git-friendly) export; None = keep all.
DEFAULT_CAPS = {"stations": None, "gps-ops": None, "geo": None, "active": 2000, "starlink": 3000, "debris": 1200}


def _is_dummy(path: Path, obj: dict) -> bool:
    note = str(obj.get("_note", "")).strip().lower()
    return path.stem.endswith("_dummy") or note.startswith("dummy")


def export_json_files(out: Path) -> list[dict]:
    entries = []
    for d in DATA_DIRS:
        for p in sorted(d.glob("*.json")):
            kind = kind_for_path(p)
            if kind not in EXPORT_KINDS:
                continue
            validate_file(p, kind)  # raises ContractError naming the field
            obj = json.loads(p.read_text(encoding="utf-8"))
            shutil.copyfile(p, out / p.name)
            entries.append({"name": p.name, "kind": kind, "dummy": _is_dummy(p, obj), "bytes": p.stat().st_size})
    return entries


def export_archetypes(out: Path) -> list[dict]:
    """Write the three fictional archetypes as contract-valid rocket.json files.

    They live in ``ascent/rockets.py`` as code, so until now nothing on the site could see them. The
    browser ascent simulator (``web/js/sim3d/ascent.js``) flies whatever rocket it is handed, and the
    specs it flies must be the same ones Python flies, so they are exported rather than retyped in
    JavaScript.
    """
    entries = []
    for rocket in list_archetypes():
        obj = rocket.to_dict()
        validate("rocket", obj)
        name = f"rocket_{rocket.id}.json"
        path = out / name
        path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
        entries.append({"name": name, "kind": "rocket", "dummy": False, "bytes": path.stat().st_size})
    return entries


def _vehicle_meta(entry: dict, out: Path) -> dict:
    """Numbers the vehicle picker needs, so it does not have to fetch all twelve rocket files.

    ``pitch_program`` is the tuned program from ``ascent.sim.DEFAULT_PITCH_PARAMS``. The nine
    design-search rockets have no program of their own, so they borrow their profile's archetype
    program and are flagged ``pitch_tuned: false`` -- the UI says so rather than implying the ascent
    was optimised for that design.
    """
    obj = json.loads((out / entry["name"]).read_text(encoding="utf-8"))
    if "stages" not in obj:  # rocket_dummy.json and anything else not vehicle-shaped
        return {}
    rocket = Rocket.from_dict(obj)
    archetype_id = PROFILE_ARCHETYPE.get(rocket.profile)
    tuned = rocket.id in DEFAULT_PITCH_PARAMS
    pitch = DEFAULT_PITCH_PARAMS.get(rocket.id) or DEFAULT_PITCH_PARAMS.get(archetype_id)
    return {
        "rocket_id": rocket.id,
        "label": rocket.name,
        "profile": rocket.profile,
        "archetype": tuned,
        "payload_kg": rocket.payload_kg,
        "liftoff_kg": rocket.liftoff_mass_kg,
        "ideal_dv_mps": round(rocket.ideal_delta_v(), 1),
        "stages": len(rocket.stages),
        "pitch_program": pitch,
        "pitch_tuned": tuned,
        "pitch_from": rocket.id if tuned else archetype_id,
    }


def _subsample(df, cap):
    if cap is None or len(df) <= cap:
        return df
    idx = np.unique(np.linspace(0, len(df) - 1, cap).round().astype(int))
    return df.iloc[idx]


def export_catalog(out: Path, keep_all: bool) -> dict:
    if OUT_PATH.exists():
        df, mode = load_catalog(OUT_PATH), "full-catalog"
    else:
        print(
            "WARNING: data/processed/catalog.parquet missing; exporting the committed offline sample", file=sys.stderr
        )
        df, mode = build_catalog(sources_from_sample()[0])[0], "offline-sample"
    total = len(df)
    parts = []
    for g in GROUP_ORDER:
        sub = df[df["group"] == g].sort_values("norad_id")
        parts.append(sub if keep_all else _subsample(sub, DEFAULT_CAPS.get(g)))
    shown = np.concatenate([p["norad_id"].to_numpy() for p in parts]).size
    objects = []
    for g, part in zip(GROUP_ORDER, parts, strict=True):
        gi = GROUP_ORDER.index(g)
        # Objects numbered >= 100000 have no TLE; row_to_tle renders their OMM elements under a placeholder catalog
        # number (propagation only: the true NORAD id is the first field).
        objects += [[int(r["norad_id"]), r["name"], gi, *row_to_tle(r)] for r in part.to_dict("records")]
    omm_only = sum(1 for part in parts for v in part["tle1"] if is_missing(v))
    fetched = max(df["epoch_utc"])  # latest TLE epoch in the export
    payload = {
        "_note": (
            "REAL CelesTrak elements (or the committed offline sample if catalog_mode says so); subsampled per group. "
            "Objects numbered >= 100000 carry a propagation TLE with a placeholder catalog number."
        ),
        "catalog_mode": mode,
        "groups": GROUP_ORDER,
        "total_objects": int(total),
        "shown_objects": int(shown),
        "omm_only_objects": int(omm_only),
        "latest_tle_epoch_utc": fetched,
        "objects": objects,
    }
    (out / "catalog_web.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return {k: v for k, v in payload.items() if k != "objects"}


def export_library(out: Path) -> dict:
    """Copy the Phase 2 vehicle and pad library into the static site.

    The mission wizard (WP2-07) needs real vehicles, their pads and each vehicle's MEASURED
    sim-vs-published error. With the local API running it reads them from /api/library; without one
    -- the public deploy -- it reads these files instead, so the wizard still shows real data and
    only the Launch button is unavailable. Writing them here is what keeps those two paths honest:
    both serve the same bytes.
    """
    lib = {}
    for folder, kind in (("rockets", "vehicle"), ("pads", "pad")):
        items = []
        for src in sorted((REPO_ROOT / "data" / folder).glob("*.json")):
            obj = json.loads(src.read_text(encoding="utf-8"))
            validate(kind, obj)
            items.append(obj)
        (out / f"{folder}.json").write_text(json.dumps(items, separators=(",", ":")), encoding="utf-8")
        lib[folder] = len(items)
    return lib


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--all", action="store_true", help="export every catalog object (large)")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        files = export_json_files(args.out)
        files += export_archetypes(args.out)
    except ContractError as e:
        print(f"ERROR: refusing to export an invalid file:\n{e}", file=sys.stderr)
        return 1
    # Fold the vehicle numbers into each rocket entry so the 3D view's picker is one fetch.
    for entry in files:
        if entry["kind"] == "rocket" and not entry["dummy"]:
            entry.update(_vehicle_meta(entry, args.out))
    files.sort(key=lambda f: (f["kind"], f["dummy"], f["name"]))
    lib = export_library(args.out)
    cat = export_catalog(args.out, args.all)
    manifest = {
        "files": files,
        "catalog": {"name": "catalog_web.json", **cat},
        "library": {"rockets": "rockets.json", "pads": "pads.json", **lib},
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    dummy = sum(f["dummy"] for f in files)
    print(
        f"exported {len(files)} json files ({dummy} dummy), {lib['rockets']} vehicles + {lib['pads']} pads, "
        f"catalog {cat['shown_objects']}/{cat['total_objects']} objects ({cat['catalog_mode']}) -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
