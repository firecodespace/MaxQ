"""Write the cited launch-vehicle and pad library (WP2-01) into data/rockets and data/pads.

    python scripts/build_rocket_library.py

Every number below was read from the cited page on the retrieval date and typed in here with its
source; nothing is recalled from memory (AGENTS.md section 11, rule 6). The library is small on
purpose: only vehicles the ascent simulator can actually fly, which today means two serial stages
and no strap-on boosters. That rules out Soyuz-2 (parallel staging) and the crewed Atlas V N22
(two solid boosters); they are listed under EXCLUDED with the reason rather than forced in.

Modelling conventions, applied the same way to every vehicle and repeated in each file's notes:

* The simulator has ONE thrust and ONE specific impulse per stage. For a first stage the sources
  give sea-level and vacuum values; the file stores their mean. This is an assumption, not data.
* `cd` (0.3) is a modelling assumption; no source publishes it.
* `area_m2` is the circle of the published core diameter.
* Fairing mass and its jettison are not modelled, so simulated payload figures are not expected to
  match the published ones closely. `scripts/validate_rocket_library.py` measures the gap and
  writes it into each file.

Running this script rewrites the files and DROPS any `sim_validation` block; run the validation
script afterwards.

Owner: Yash (lane: data/rockets, data/pads, scripts/).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from contracts.validate import validate  # noqa: E402

RETRIEVED = "2026-11-11T00:00:00Z"
G0 = 9.80665
CD_ASSUMED = 0.3


def area(d_m: float) -> float:
    return round(math.pi * (d_m / 2) ** 2, 3)


def src(title: str, url: str, note: str = "") -> dict:
    s = {"title": title, "url": url, "retrieved_utc": RETRIEVED}
    if note:
        s["note"] = note
    return s


CONVENTIONS = (
    "Modelling conventions (assumptions, not data): first-stage thrust and Isp are the mean of the published "
    "sea-level and vacuum values because the simulator has one of each per stage; cd = 0.3 is assumed; area is the "
    "circle of the published core diameter; fairing mass and jettison are not modelled."
)

VEHICLES = [
    {
        "schema_version": 1,
        "id": "falcon9_block5",
        "name": "Falcon 9 Block 5 (expendable)",
        "kind": "real",
        "crew_rated": True,
        "profile": "crew_iss",
        "max_q_limit_kpa": None,
        "stages": [
            {
                "name": "First stage, 9 x Merlin 1D",
                "engines": 9,
                "dry_kg": 25600.0,
                "prop_kg": 395700.0,
                "thrust_n": round((7607e3 + 8227e3) / 2),
                "isp_s": round((283 + 312) / 2, 1),
                "cd": CD_ASSUMED,
                "area_m2": area(3.7),
            },
            {
                "name": "Second stage, 1 x Merlin 1D Vacuum",
                "engines": 1,
                "dry_kg": 3900.0,
                "prop_kg": 92670.0,
                "thrust_n": 981e3,
                "isp_s": 348.0,
                "cd": CD_ASSUMED,
                "area_m2": area(3.7),
            },
        ],
        "payload_to_leo_kg_published": 22800.0,
        "sources": [
            src(
                "SpaceX: Falcon 9 vehicle page",
                "https://www.spacex.com/vehicles/falcon-9/",
                "mass 549,054 kg, payload to LEO 22,800 kg, second-stage thrust 981 kN, burn 397 s, diameter 3.7 m",
            ),
            src(
                "Wikipedia: Falcon 9 (stage specification tables)",
                "https://en.wikipedia.org/wiki/Falcon_9",
                "stage empty and propellant masses, sea-level/vacuum thrust and Isp, burn times",
            ),
            src(
                "Wevolver: Falcon 9 v1.2 / Block 5 specs",
                "https://www.wevolver.com/specs/falcon-9-v12-or-full-thrust-block-5",
                "same stage table; independent copy used as a cross-check",
            ),
            src(
                "Wikipedia: Falcon 9 Block 5",
                "https://en.wikipedia.org/wiki/Falcon_9_Block_5",
                "payload to LEO at 28.5 deg: 22,800 kg expended, 18,500 kg reusable",
            ),
        ],
        "confidence": "approximate",
        "notes": (
            "Crew-capable class: SpaceX describes Falcon 9 as transporting people, and it launches Crew Dragon. "
            
                "Conflicts between sources: second-stage thrust is 981 kN on spacex.com and in the stage table but "
                "934 kN in"
            "the Wikipedia infobox (981 kN used, manufacturer value). The stage masses sum to 517,870 kg against a "
            
                "published lift-off mass of 549,054 kg; the difference (about 31 t) is consistent with payload plus "
                "fairing"
            "but is not itemised by any source. The published 22,800 kg is the expendable figure to a 28.5 deg orbit; "
            "no altitude is stated. No source publishes a max-q limit. " + CONVENTIONS
        ),
    },
    {
        "schema_version": 1,
        "id": "atlas_v_401",
        "name": "Atlas V 401",
        "kind": "real",
        "crew_rated": False,
        "profile": "cargo_iss",
        "max_q_limit_kpa": None,
        "stages": [
            {
                "name": "Common Core Booster, 1 x RD-180",
                "engines": 1,
                "dry_kg": 21054.0,
                "prop_kg": 284089.0,
                "thrust_n": round((3827e3 + 4152e3) / 2),
                "isp_s": round((311.3 + 337.8) / 2, 2),
                "cd": CD_ASSUMED,
                "area_m2": area(3.81),
            },
            {
                "name": "Centaur III, 1 x RL10",
                "engines": 1,
                "dry_kg": 2316.0,
                "prop_kg": 20830.0,
                "thrust_n": 99.2e3,
                "isp_s": 450.5,
                "cd": CD_ASSUMED,
                "area_m2": area(3.81),
            },
        ],
        "payload_to_leo_kg_published": 9800.0,
        "sources": [
            src(
                "ULA: Atlas V",
                "https://www.ulalaunch.com/rockets/atlas-v",
                
                    "401 performance: 9,800 kg to LEO (200 km circular, 28.7 deg), 8,910 kg to ISS orbit (407 km, "
                    "51.6 deg);"
                "RD-180 sea-level Isp 311 s; RL10 Isp 450.5 s",
            ),
            src(
                "Wikipedia: Atlas V",
                "https://en.wikipedia.org/wiki/Atlas_V",
                "CCB empty 21,054 kg, propellant 284,089 kg, thrust 3,827 kN SL / 4,152 kN vac, Isp 311.3 / 337.8 s; "
                "Centaur III empty 2,316 kg, propellant 20,830 kg, thrust 99.2 kN, Isp 450.5 s, diameter 3.81 m",
            ),
        ],
        "confidence": "approximate",
        "notes": (
            "Not crew rated in this configuration: the crewed Atlas V is the N22 (two solid boosters, dual-engine "
            
                "Centaur), which the two-serial-stage simulator cannot represent, so it is not in the library. The "
                "Centaur's"
            "area uses the core diameter (3.81 m), not its own 3.05 m, because it flies inside the fairing. The 4 m "
            
                "fairing mass is not in either source. The published 9,800 kg is to 200 km circular at 28.7 deg "
                "(ULA), which"
            "is the same reference orbit the validation uses. Source status: 'Active, retiring'. "
            "KNOWN MODEL LIMITATION, measured not assumed: the ascent simulator reaches only 4,410 kg here, 55 % "
            "below the published 9,800 kg, and that is the simulator's fault rather than the data's. Centaur has a "
            "thrust-to-weight of 0.31 at staging and would burn for 928 s continuously in this model, because the "
            "model has no coast and no second burn. The real flight coasts and relights; burning a 0.31 g stage "
            "straight through instead pours the margin into gravity losses. The other two vehicles, whose upper "
            "stages sit at 0.84 and 1.41, come out within tolerance. Expect this to improve when WP2-02's MissionEnv "
            "replaces the single continuous burn with discrete burns and coasts. " + CONVENTIONS
        ),
    },
    {
        "schema_version": 1,
        "id": "antares_230plus",
        "name": "Antares 230+ (retired 2023)",
        "kind": "real",
        "crew_rated": False,
        "profile": "cargo_iss",
        "max_q_limit_kpa": None,
        "stages": [
            {
                "name": "First stage, 2 x RD-181",
                "engines": 2,
                "dry_kg": 20600.0,
                "prop_kg": 262600.0 - 20600.0,
                "thrust_n": 3844e3,
                "isp_s": round((311.9 + 339.2) / 2, 2),
                "cd": CD_ASSUMED,
                "area_m2": area(3.9),
            },
            {
                "name": "Castor 30XL (solid)",
                "engines": 1,
                "dry_kg": 26300.0 - 24200.0,
                "prop_kg": 24200.0,
                "thrust_n": 474e3,
                "isp_s": round(474e3 * 156 / (24200.0 * G0), 1),
                "cd": CD_ASSUMED,
                "area_m2": area(3.9),
            },
        ],
        "payload_to_leo_kg_published": 8000.0,
        "sources": [
            src(
                "Wikipedia: Antares (rocket)",
                "https://en.wikipedia.org/wiki/Antares_(rocket)",
                "200-series first stage empty 20,600 kg, gross 262,600 kg, thrust 3,844 kN, Isp 311.9 / 339.2 s, burn "
                "215 s; Castor 30XL gross 26,300 kg, propellant 24,200 kg, thrust 474 kN, burn 156 s; payload to LEO "
                "8,000 kg; diameter 3.9 m; launch site MARS LP-0A; 230+ last flew 2 August 2023",
            ),
        ],
        "confidence": "approximate",
        "notes": (
            "Single source, so nothing here is cross-checked. Derived, not published: first-stage propellant = gross - "
            
                "empty (242,000 kg); second-stage dry = gross - propellant (2,100 kg); second-stage Isp = thrust x "
                "burn time"
            
                "/ (propellant x g0) = 311.6 s, which treats the quoted MAXIMUM thrust as constant and is therefore "
                "an upper"
            "estimate for a solid motor. The source gives one first-stage thrust figure (3,844 kN) without saying "
            "sea-level or vacuum, so it is used as is rather than averaged. The 8,000 kg figure has no stated orbit. "
            "The vehicle is retired; it is in the library as a real cargo-to-ISS launcher with a different pad and "
            "latitude from the other two. " + CONVENTIONS
        ),
    },
]

EXCLUDED = {
    "soyuz_2_1a": 
        "parallel staging (four strap-on boosters burning with the core); the simulator flies two serial stages",
    "atlas_v_n22": "two solid rocket boosters and a dual-engine Centaur; same limitation",
}

PADS = [
    {
        "schema_version": 1,
        "id": "ksc_lc39a",
        "name": "Kennedy Space Center LC-39A",
        "site": "Kennedy Space Center",
        "lat_deg": 28.60833,
        "lon_deg": -80.60444,
        "supported_rocket_ids": ["falcon9_block5"],
        "azimuth_range_deg": None,
        "sources": [
            src(
                "Wikipedia: Kennedy Space Center Launch Complex 39A",
                "https://en.wikipedia.org/wiki/Kennedy_Space_Center_Launch_Complex_39A",
                "coordinates 28.60833 N, 80.60444 W; current rockets Falcon 9, Falcon Heavy; orbital "
                "inclination range 28.5-55 and 66-145 deg",
            )
        ],
        "confidence": "approximate",
        "notes": "The source gives an orbital INCLINATION range (28.5-55, 66-145 deg), not a launch azimuth range, so "
        "azimuth_range_deg is null rather than converted.",
    },
    {
        "schema_version": 1,
        "id": "ccsfs_slc40",
        "name": "Cape Canaveral SLC-40",
        "site": "Cape Canaveral Space Force Station",
        "lat_deg": 28.5619,
        "lon_deg": -80.5772,
        "supported_rocket_ids": ["falcon9_block5"],
        "azimuth_range_deg": None,
        "sources": [
            src(
                "Wikipedia: Cape Canaveral Space Launch Complex 40",
                "https://en.wikipedia.org/wiki/Cape_Canaveral_Space_Launch_Complex_40",
                "coordinates 28.5619 N, 80.5772 W; current rocket Falcon 9; orbital inclination range 28.5-55 "
                "and 66-145 deg",
            )
        ],
        "confidence": "approximate",
        "notes": 
            "Same coordinates as the v1 'Cape Canaveral' site in mission/sites.py, so the existing ISS windows apply."
        "Inclination range given, not azimuth: azimuth_range_deg is null.",
    },
    {
        "schema_version": 1,
        "id": "ccsfs_slc41",
        "name": "Cape Canaveral SLC-41",
        "site": "Cape Canaveral Space Force Station",
        "lat_deg": 28.58333,
        "lon_deg": -80.58306,
        "supported_rocket_ids": ["atlas_v_401"],
        "azimuth_range_deg": None,
        "sources": [
            src(
                "Wikipedia: Cape Canaveral Space Launch Complex 41",
                "https://en.wikipedia.org/wiki/Cape_Canaveral_Space_Launch_Complex_41",
                "coordinates 28.58333 N, 80.58306 W; current rockets Vulcan, Atlas V; orbital inclination "
                "range 28-57 deg",
            )
        ],
        "confidence": "approximate",
        "notes": "Inclination range given (28-57 deg), not azimuth: azimuth_range_deg is null.",
    },
    {
        "schema_version": 1,
        "id": "mars_lp0a",
        "name": "Mid-Atlantic Regional Spaceport LP-0A",
        "site": "Wallops Island, Virginia",
        "lat_deg": 37.84333,
        "lon_deg": -75.47806,
        "supported_rocket_ids": ["antares_230plus"],
        "azimuth_range_deg": None,
        "sources": [
            src(
                "Wikipedia: Mid-Atlantic Regional Spaceport",
                "https://en.wikipedia.org/wiki/Mid-Atlantic_Regional_Spaceport",
                "spaceport coordinates 37.84333 N, 75.47806 W; LP-0A associated rockets: Antares 100- and "
                "200-series (retired); status 'undergoing renovation'",
            )
        ],
        "confidence": "approximate",
        "notes": "Coordinates are the SPACEPORT's, not the pad's own: the page lists several pad coordinates without "
        "labelling which is LP-0A (they differ by about 1 km). No azimuth or inclination range is given. There "
        "are no v1 launch windows for this site yet.",
    },
]


def main() -> int:
    for folder, kind, items in (("rockets", "vehicle", VEHICLES), ("pads", "pad", PADS)):
        out = REPO_ROOT / "data" / folder
        out.mkdir(parents=True, exist_ok=True)
        for obj in items:
            validate(kind, obj)
            (out / f"{obj['id']}.json").write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
            print(f"wrote data/{folder}/{obj['id']}.json")
    ids = {v["id"] for v in VEHICLES}
    for p in PADS:
        missing = set(p["supported_rocket_ids"]) - ids
        if missing:
            print(f"ERROR: pad {p['id']} refers to unknown vehicles {sorted(missing)}", file=sys.stderr)
            return 1
    print("excluded (simulator cannot fly them yet): " + "; ".join(f"{k}: {v}" for k, v in EXCLUDED.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
